"""FastAPI route tests via TestClient. Auth, gating and the SSE contract.

Runs against an isolated temp database with auth forced on and email in
dry-run, so nothing external is touched. No Gemini calls: the one chat test
stubs the agent turn.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import multi_agent_system.config as config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="mas_api_"))
config.DB_PATH = _TMP / "api.db"
config.AUTH_ENABLED = True
config.AUTH_ALLOW_SIGNUP = False
config.AUTH_SIGNUP_CODE = ""
config.SECRET_KEY = "test-secret-key"
config.EMAIL_ENABLED = False

import multi_agent_system.db.database as database  # noqa: E402

database.DB_PATH = config.DB_PATH
database.init_db(force=True)

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
from multi_agent_system import accounts  # noqa: E402

# Keep app-level constants in sync with the patched config.
app_module.AUTH_COOKIE  # noqa: B018 - ensure the module imported cleanly


def fresh_client() -> TestClient:
    return TestClient(app_module.app)


class TestApiAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        database.init_db(force=True)
        # Bootstrap the first admin directly, bypassing the network.
        accounts.create("boss", "boss-password-1", is_admin=True)

    def test_health_is_public_and_minimal_when_anonymous(self):
        c = fresh_client()
        r = c.get("/api/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("status", body)
        self.assertNotIn("config", body)  # no operator detail for anonymous

    def test_protected_routes_reject_anonymous(self):
        c = fresh_client()
        for method, path in [
            ("get", "/api/meta"),
            ("post", "/api/reset"),
            ("get", "/api/accounts"),
            ("get", "/api/download?f=x.docx"),
        ]:
            r = c.post(path, json={}) if method == "post" else c.get(path)
            self.assertEqual(r.status_code, 401, f"{method} {path}")

    def test_login_logout_cycle(self):
        c = fresh_client()
        self.assertEqual(
            c.post("/api/auth/login", json={"username": "boss", "password": "wrong"}).status_code, 401
        )
        r = c.post("/api/auth/login", json={"username": "boss", "password": "boss-password-1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["is_admin"])
        self.assertEqual(c.get("/api/meta").status_code, 200)
        self.assertEqual(c.post("/api/auth/logout", json={}).status_code, 200)
        self.assertEqual(c.get("/api/meta").status_code, 401)

    def test_signup_is_closed_after_bootstrap(self):
        c = fresh_client()
        r = c.post("/api/auth/signup", json={"username": "sneak", "password": "password-123"})
        self.assertEqual(r.status_code, 403)

    def test_admin_only_routes(self):
        # Create a non-admin and confirm they cannot reach /api/accounts.
        accounts.create("peon", "peon-password-1", is_admin=False)
        c = fresh_client()
        c.post("/api/auth/login", json={"username": "peon", "password": "peon-password-1"})
        self.assertEqual(c.get("/api/accounts").status_code, 403)
        self.assertEqual(
            c.post("/api/accounts", json={"username": "x", "password": "y-123456789"}).status_code, 403
        )

    def test_admin_can_manage_accounts(self):
        c = fresh_client()
        c.post("/api/auth/login", json={"username": "boss", "password": "boss-password-1"})
        before = len(c.get("/api/accounts").json()["accounts"])
        r = c.post("/api/accounts", json={"username": "newbie", "password": "newbie-password-1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(c.get("/api/accounts").json()["accounts"]), before + 1)
        self.assertEqual(c.delete("/api/accounts/newbie").status_code, 200)

    def test_login_rotates_the_conversation_session(self):
        """A new sign-in must not inherit the previous session's chat memory."""
        c = fresh_client()
        c.post("/api/auth/login", json={"username": "boss", "password": "boss-password-1"})
        first = c.cookies.get(app_module.SESSION_COOKIE)
        # Establish a session id, then log in again — the session must change.
        c.post("/api/reset", json={})
        established = c.cookies.get(app_module.SESSION_COOKIE) or first
        c.post("/api/auth/login", json={"username": "boss", "password": "boss-password-1"})
        rotated = c.cookies.get(app_module.SESSION_COOKIE)
        self.assertIsNotNone(rotated)
        self.assertNotEqual(rotated, established)

    def test_admin_cannot_delete_self(self):
        c = fresh_client()
        c.post("/api/auth/login", json={"username": "boss", "password": "boss-password-1"})
        self.assertEqual(c.delete("/api/accounts/boss").status_code, 400)

    def test_change_password_requires_current(self):
        accounts.create("changer", "old-password-1", is_admin=False)
        c = fresh_client()
        c.post("/api/auth/login", json={"username": "changer", "password": "old-password-1"})
        bad = c.post("/api/auth/password", json={"current_password": "nope", "new_password": "new-password-1"})
        self.assertEqual(bad.status_code, 403)
        ok = c.post("/api/auth/password",
                    json={"current_password": "old-password-1", "new_password": "new-password-1"})
        self.assertEqual(ok.status_code, 200)

    def test_download_traversal_is_blocked(self):
        c = fresh_client()
        c.post("/api/auth/login", json={"username": "boss", "password": "boss-password-1"})
        self.assertEqual(c.get("/api/download", params={"f": "../.env"}).status_code, 404)


class TestApiSignupGating(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        database.init_db(force=True)

    def test_first_signup_bootstraps_admin(self):
        c = fresh_client()
        self.assertTrue(c.get("/api/auth/me").json()["first_run"])
        r = c.post("/api/auth/signup", json={"username": "founder", "password": "founder-password-1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["is_admin"])
        # Second signup is now closed.
        self.assertEqual(
            c.post("/api/auth/signup", json={"username": "later", "password": "later-password-1"}).status_code,
            403,
        )

    def test_open_signup_with_code(self):
        accounts.create("owner", "owner-password-1", is_admin=True)
        config.AUTH_ALLOW_SIGNUP = True
        config.AUTH_SIGNUP_CODE = "let-me-in"
        try:
            c = fresh_client()
            self.assertEqual(
                c.post("/api/auth/signup",
                       json={"username": "guest", "password": "guest-password-1", "code": "wrong"}).status_code,
                403,
            )
            ok = c.post("/api/auth/signup",
                        json={"username": "guest", "password": "guest-password-1", "code": "let-me-in"})
            self.assertEqual(ok.status_code, 200)
            self.assertFalse(ok.json()["is_admin"])  # non-first signups are not admin
        finally:
            config.AUTH_ALLOW_SIGNUP = False
            config.AUTH_SIGNUP_CODE = ""


class TestApiPasswordReset(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        database.init_db(force=True)
        config.EMAIL_ENABLED = False  # reset email logged, never delivered
        accounts.create("resetme", "old-password-1", email="me@example.com")

    def test_forgot_is_generic_and_reset_works(self):
        c = fresh_client()
        # Unknown identifier and a real one both return the same generic 200,
        # so the endpoint never reveals which accounts exist.
        self.assertEqual(c.post("/api/auth/forgot", json={"identifier": "nobody"}).status_code, 200)
        self.assertEqual(c.post("/api/auth/forgot", json={"identifier": "resetme"}).status_code, 200)

        # Mint a valid reset token the way the endpoint does, then reset with it.
        from multi_agent_system import auth

        acct = accounts.get("resetme")
        token = auth.issue_reset_token("resetme", config.SECRET_KEY, 1800, accounts.token_salt(acct))
        r = c.post("/api/auth/reset", json={"token": token, "new_password": "brand-new-1"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(accounts.verify("resetme", "brand-new-1"))
        self.assertIsNone(accounts.verify("resetme", "old-password-1"))
        # The same token cannot be reused — the salt has rotated.
        again = c.post("/api/auth/reset", json={"token": token, "new_password": "another-1"})
        self.assertEqual(again.status_code, 400)

    def test_reset_rejects_garbage_token(self):
        c = fresh_client()
        self.assertEqual(
            c.post("/api/auth/reset", json={"token": "not-a-token", "new_password": "whatever-1"}).status_code,
            400,
        )

    def test_forgot_without_recovery_email_still_generic(self):
        accounts.create("noemail", "some-password-1")
        c = fresh_client()
        # No recovery email on file → nothing sent, but still a generic 200.
        self.assertEqual(c.post("/api/auth/forgot", json={"identifier": "noemail"}).status_code, 200)


class TestApiChatStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        database.init_db(force=True)
        accounts.create("boss", "boss-password-1", is_admin=True)

    def test_stream_shape_with_stubbed_agent(self):
        """The SSE contract (start → trace → done) without calling Gemini."""
        from multi_agent_system.core.runtime import TraceEvent

        c = fresh_client()
        c.post("/api/auth/login", json={"username": "boss", "password": "boss-password-1"})

        # Login rotates the conversation session, so stub the agent on whichever
        # session the client now holds (its cookie), not a freshly-minted one.
        sid = c.cookies.get(app_module.SESSION_COOKIE)
        session = app_module.store.get_or_create(sid)

        def fake_chat(message, trace=None):
            if trace is not None:
                trace.append(TraceEvent(agent="Master Agent", kind="delegation",
                                        name="delegate_to_user_management_agent", args={}))
            return "Here are the users."

        original = session.agent.chat
        session.agent.chat = fake_chat
        try:
            with c.stream("POST", "/api/chat/stream", json={"message": "list users"}) as r:
                self.assertEqual(r.status_code, 200)
                frames = [line[6:] for line in r.iter_lines() if line.startswith("data: ")]
        finally:
            session.agent.chat = original

        import json as _json
        types = [_json.loads(f)["type"] for f in frames]
        self.assertEqual(types[0], "start")
        self.assertIn("done", types)
        done = _json.loads([f for f in frames if '"done"' in f][-1])
        self.assertEqual(done["answer"], "Here are the users.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
