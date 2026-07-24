"""Authentication: password verification and signed session tokens.

Deliberately small and dependency-free:

* Passwords are stored as scrypt hashes (`AUTH_PASSWORD_HASH`). A plaintext
  `AUTH_PASSWORD` is accepted for local convenience and hashed on load, so the
  verification path is identical either way.
* Session tokens are stateless and signed with HMAC-SHA256 over the username and
  an expiry, so a token cannot be forged or extended, and no server-side session
  table is needed for login state.
* Every comparison uses `hmac.compare_digest` to avoid leaking equality timing.

Generate a hash for deployment with:

    python -m multi_agent_system.auth
"""

import base64
import hashlib
import hmac
import secrets
import threading
import time
from typing import Optional, Tuple

SCRYPT_N = 2 ** 14  # ~100ms per hash: slow enough to matter, fast enough to serve
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LEN = 32

# A structurally valid hash of a random secret. verify_password() is run against
# this when a username does not exist, so a missing user costs the same time as a
# wrong password and cannot be told apart by timing.
DUMMY_HASH = (
    "scrypt$ZHVtbXlzYWx0ZHVtbXlzYQ$"
    "ZHVtbXloYXNoZHVtbXloYXNoZHVtbXloYXNoZHVtbXloYXNo"
)


# --- password hashing --------------------------------------------------------


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """Return "scrypt$<salt>$<hash>", both parts base64url-encoded."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_LEN
    )
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")  # noqa: E731
    return f"scrypt${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored scrypt hash."""
    if not password or not stored:
        return False
    try:
        scheme, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        salt = base64.urlsafe_b64decode(pad(salt_b64))
        expected = base64.urlsafe_b64decode(pad(hash_b64))
    except (ValueError, TypeError):
        return False

    candidate = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=len(expected),
    )
    return hmac.compare_digest(candidate, expected)


# --- session tokens ----------------------------------------------------------


def _sign(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_token(username: str, secret: str, max_age: int, salt: str = "") -> str:
    """Mint a signed token that expires on its own.

    `salt` is a per-user component (see accounts.token_salt) folded into the
    signature but not carried in the token, so rotating it — e.g. on a password
    change — invalidates every token issued before the change.
    """
    expiry = int(time.time()) + max_age
    payload = f"{username}|{expiry}"
    return f"{payload}|{_sign(secret, f'{payload}|{salt}')}"


def read_token(token: str, secret: str, salt: str = "") -> Optional[str]:
    """Return the username if the token is authentic and unexpired, else None."""
    if not token or not secret:
        return None
    parts = token.rsplit("|", 2)
    if len(parts) != 3:
        return None
    username, expiry, signature = parts
    if not hmac.compare_digest(_sign(secret, f"{username}|{expiry}|{salt}"), signature):
        return None
    try:
        if int(expiry) < time.time():
            return None
    except ValueError:
        return None
    return username


def token_username(token: str) -> Optional[str]:
    """Read the claimed username without verifying — used only to find the salt."""
    parts = (token or "").rsplit("|", 2)
    return parts[0] if len(parts) == 3 else None


# Password-reset tokens reuse the same signed-token machinery, but the per-user
# salt is namespaced with a purpose so a login cookie can never be replayed as a
# reset token (and vice versa). Binding to the account's password-hash salt also
# makes each reset token single-use: once the password changes, the salt changes
# and the token dies.
def issue_reset_token(username: str, secret: str, max_age: int, account_salt: str) -> str:
    return issue_token(username, secret, max_age, salt=f"reset|{account_salt}")


def read_reset_token(token: str, secret: str, account_salt: str) -> Optional[str]:
    return read_token(token, secret, salt=f"reset|{account_salt}")


# --- brute-force protection --------------------------------------------------


class AttemptLimiter:
    """Locks a key out after repeated failures, with a fixed cooldown."""

    def __init__(self, limit: int = 6, window: float = 300.0):
        self.limit, self.window = limit, window
        self._fails = {}
        self._guard = threading.Lock()

    def blocked(self, key: str) -> Tuple[bool, int]:
        """Return (is_blocked, seconds_remaining)."""
        now = time.monotonic()
        with self._guard:
            first, count = self._fails.get(key, (now, 0))
            if now - first > self.window:
                self._fails.pop(key, None)
                return False, 0
            if count >= self.limit:
                return True, int(self.window - (now - first)) + 1
            return False, 0

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._guard:
            first, count = self._fails.get(key, (now, 0))
            if now - first > self.window:
                first, count = now, 0
            self._fails[key] = (first, count + 1)
            if len(self._fails) > 5000:  # bound memory against random-source floods
                self._fails = {
                    k: v for k, v in self._fails.items() if now - v[0] < self.window
                }

    def clear(self, key: str) -> None:
        with self._guard:
            self._fails.pop(key, None)


if __name__ == "__main__":  # pragma: no cover - operator helper
    import getpass

    print("Generate an AUTH_PASSWORD_HASH for your .env / secret manager.\n")
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Confirm:  ")
    if first != second:
        raise SystemExit("Passwords do not match.")
    if len(first) < 12:
        print("Warning: shorter than 12 characters is weak for an internet-facing app.")
    print(f"\nAUTH_PASSWORD_HASH={hash_password(first)}\n")
