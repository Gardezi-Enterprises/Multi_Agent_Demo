"""Central configuration, loaded once from the environment (.env)."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent

load_dotenv(ROOT_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --- Google Gen AI SDK -------------------------------------------------------
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()

# Additional keys are used strictly as fallbacks: the runtime only rotates to
# the next one when the current key's *daily* free-tier quota is exhausted.
API_KEYS = [
    key for key in (GOOGLE_API_KEY, os.getenv("GOOGLE_API_KEY_2", "").strip()) if key
]

# --- Database ----------------------------------------------------------------
# Only sqlite is supported; DATABASE_URL is parsed for the file path.
_db_url = os.getenv("DATABASE_URL", "sqlite:///multi_agent.db").strip()
DB_PATH = ROOT_DIR / _db_url.replace("sqlite:///", "")

# --- Email -------------------------------------------------------------------
# Transport for outgoing mail: dry_run | smtp | resend | sendgrid | brevo.
# EMAIL_ENABLED=false forces dry_run regardless, as a single global off switch.
EMAIL_ENABLED = _bool("EMAIL_ENABLED", False)
EMAIL_PROVIDER = (os.getenv("EMAIL_PROVIDER", "smtp").strip().lower() or "smtp")
if not EMAIL_ENABLED:
    EMAIL_PROVIDER = "dry_run"

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "").strip()
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
# SMTP_PASS is accepted as an alias, since providers label it either way.
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or os.getenv("SMTP_PASS") or "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "").strip() or SMTP_USER

# The From address, whichever transport is used. EMAIL_FROM is the provider-
# neutral name; SMTP_FROM/SMTP_USER remain valid fallbacks.
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip() or SMTP_FROM

# Port 465 is implicit TLS (SMTPS) and must not be upgraded with STARTTLS;
# 587 and 25 start plain and are upgraded. Overridable for unusual servers.
_ssl_env = os.getenv("SMTP_USE_SSL", "").strip()
SMTP_USE_SSL = _bool("SMTP_USE_SSL", False) if _ssl_env else SMTP_PORT == 465

# --- Output ------------------------------------------------------------------
OUTPUT_DIR = ROOT_DIR / os.getenv("OUTPUT_DIR", "output").strip()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = ROOT_DIR / os.getenv("UPLOAD_DIR", "uploads").strip()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


# --- Runtime -----------------------------------------------------------------
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT in ("production", "prod")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "text").strip().lower()

# Sessions
SESSION_MAX = _int("SESSION_MAX", 500)
SESSION_IDLE_TTL = _int("SESSION_IDLE_TTL", 3600)

# Request limits
MAX_MESSAGE_CHARS = _int("MAX_MESSAGE_CHARS", 50_000)
MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_MB", 10) * 1024 * 1024
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 1024 * 1024
REQUEST_TIMEOUT = _int("REQUEST_TIMEOUT", 300)

# Simple per-session rate limit (turns per window).
RATE_LIMIT_TURNS = _int("RATE_LIMIT_TURNS", 30)
RATE_LIMIT_WINDOW = _int("RATE_LIMIT_WINDOW", 60)

ALLOWED_UPLOAD_SUFFIXES = {
    s.strip().lower()
    for s in os.getenv(
        "ALLOWED_UPLOAD_SUFFIXES", ".pdf,.docx,.txt,.md,.rtf,.csv"
    ).split(",")
    if s.strip()
}


# --- Authentication ----------------------------------------------------------
# Off by default so local development is frictionless; validate() refuses to
# start with it off in production, so exposure is never the silent default.
AUTH_ENABLED = _bool("AUTH_ENABLED", False)
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin").strip()
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "").strip()
AUTH_PASSWORD_HASH = os.getenv("AUTH_PASSWORD_HASH", "").strip()
AUTH_MAX_AGE = _int("AUTH_MAX_AGE", 12 * 3600)
AUTH_MAX_ATTEMPTS = _int("AUTH_MAX_ATTEMPTS", 6)
AUTH_LOCKOUT = _int("AUTH_LOCKOUT", 300)

# Signup is closed by default: the first account bootstraps the owner freely,
# but opening registration to everyone would hand full access to any visitor.
# Turn it on deliberately, optionally behind a shared invite code.
AUTH_ALLOW_SIGNUP = _bool("AUTH_ALLOW_SIGNUP", False)
AUTH_SIGNUP_CODE = os.getenv("AUTH_SIGNUP_CODE", "").strip()

# Password-reset link lifetime, and an optional public base URL for the link in
# the email. When unset, the URL is derived from the incoming request's origin.
RESET_TOKEN_TTL = _int("RESET_TOKEN_TTL", 1800)
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")

# Signs session tokens. A generated key is fine for one process, but every
# restart (and every replica) would invalidate existing logins, so production
# must supply its own.
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
SECRET_KEY_GENERATED = not SECRET_KEY
if not SECRET_KEY:
    import secrets as _secrets

    SECRET_KEY = _secrets.token_urlsafe(32)

# Set Secure on cookies when served over HTTPS. Defaults on in production.
COOKIE_SECURE = _bool("COOKIE_SECURE", IS_PRODUCTION)


class ConfigError(RuntimeError):
    """Raised when the configuration cannot support the requested behaviour."""


def validate(require_model: bool = True) -> list:
    """Check configuration coherence at startup.

    Returns a list of non-fatal warnings. Raises ConfigError for problems that
    would cause confusing failures later — it is better to refuse to start with
    a clear message than to fail on the first user request.
    """
    warnings = []

    if require_model and not API_KEYS:
        raise ConfigError(
            "GOOGLE_API_KEY is not set. Copy .env.example to .env and add a key "
            "from https://aistudio.google.com/apikey"
        )

    if EMAIL_ENABLED:
        if EMAIL_PROVIDER not in ("smtp", "resend", "sendgrid", "brevo"):
            raise ConfigError(
                f"EMAIL_PROVIDER='{EMAIL_PROVIDER}' is not recognised. "
                "Use one of: smtp, resend, sendgrid, brevo."
            )
        if not EMAIL_FROM:
            raise ConfigError(
                "EMAIL_ENABLED=true but no sender address is set. "
                "Set EMAIL_FROM (or SMTP_FROM/SMTP_USER)."
            )
        required = {
            "smtp": ("SMTP_HOST/SMTP_USER/SMTP_PASSWORD", bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)),
            "resend": ("RESEND_API_KEY", bool(RESEND_API_KEY)),
            "sendgrid": ("SENDGRID_API_KEY", bool(SENDGRID_API_KEY)),
            "brevo": ("BREVO_API_KEY", bool(BREVO_API_KEY)),
        }[EMAIL_PROVIDER]
        if not required[1]:
            raise ConfigError(
                f"EMAIL_PROVIDER='{EMAIL_PROVIDER}' requires {required[0]} to be set."
            )
    else:
        warnings.append(
            "Email is in dry-run mode: messages are logged, not delivered "
            "(set EMAIL_ENABLED=true to send)."
        )

    if AUTH_ENABLED:
        if not (AUTH_PASSWORD or AUTH_PASSWORD_HASH):
            raise ConfigError(
                "AUTH_ENABLED=true but no password is set. Set AUTH_PASSWORD_HASH "
                "(generate one with: python -m multi_agent_system.auth) or "
                "AUTH_PASSWORD for local use."
            )
        if IS_PRODUCTION and AUTH_PASSWORD and not AUTH_PASSWORD_HASH:
            warnings.append(
                "AUTH_PASSWORD is plaintext. Prefer AUTH_PASSWORD_HASH in production."
            )
        if IS_PRODUCTION and SECRET_KEY_GENERATED:
            raise ConfigError(
                "SECRET_KEY is not set. Without it, session tokens are signed with a "
                "key that changes on restart, logging everyone out. Set a stable "
                "random value (e.g. `python -c \"import secrets;print(secrets.token_urlsafe(32))\"`)."
            )
    elif IS_PRODUCTION:
        # Fail closed: an unauthenticated production deployment exposes the user
        # database and the ability to send mail from the configured account.
        raise ConfigError(
            "AUTH_ENABLED=false in production. This would let anyone with the URL "
            "read the user database and send email from your account. Set "
            "AUTH_ENABLED=true with AUTH_PASSWORD_HASH, or put the service behind "
            "an identity proxy and set AUTH_ENABLED=false explicitly with "
            "ENVIRONMENT=staging."
        )
    else:
        warnings.append(
            "Authentication is off — anyone who can reach this port has full access."
        )

    if IS_PRODUCTION and LOG_FORMAT != "json":
        warnings.append("ENVIRONMENT=production but LOG_FORMAT is not 'json'.")

    return warnings


def summary() -> dict:
    """Non-secret configuration, safe to log or expose on a health endpoint."""
    return {
        "environment": ENVIRONMENT,
        "model": GEMINI_MODEL,
        "api_keys_configured": len(API_KEYS),
        "email_enabled": EMAIL_ENABLED,
        "email_provider": EMAIL_PROVIDER,
        "email_from": EMAIL_FROM or None,
        "database": DB_PATH.name,
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "auth_enabled": AUTH_ENABLED,
    }
