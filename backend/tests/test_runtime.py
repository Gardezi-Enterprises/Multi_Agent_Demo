"""Tests for the production runtime: sessions, config validation and the
server's pure helpers. No model calls and no network access.

Run with:  python tests/test_runtime.py     (or: pytest tests/)
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from multi_agent_system import config  # noqa: E402
from multi_agent_system.sessions import SessionStore  # noqa: E402


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore(max_sessions=5, idle_ttl=60)

    def test_each_session_gets_its_own_agent(self):
        """The defect this store exists to prevent: shared conversation state."""
        a = self.store.get_or_create()
        b = self.store.get_or_create()
        self.assertNotEqual(a.id, b.id)
        self.assertIsNot(a.agent, b.agent)
        self.assertIsNot(a.agent.history, b.agent.history)
        self.assertIsNot(a.lock, b.lock)

    def test_known_id_returns_the_same_session(self):
        a = self.store.get_or_create()
        again = self.store.get_or_create(a.id)
        self.assertIs(a, again)

    def test_unknown_id_is_honoured_so_reloads_keep_context(self):
        session = self.store.get_or_create("client-supplied-id")
        self.assertEqual(session.id, "client-supplied-id")
        self.assertIs(self.store.get_or_create("client-supplied-id"), session)

    def test_capacity_evicts_least_recently_used(self):
        keep = self.store.get_or_create()
        for _ in range(5):
            time.sleep(0.001)
            self.store.get_or_create()
            keep.touch()  # keep it the most recently used
        self.assertLessEqual(self.store.stats()["active_sessions"], 5)
        self.assertIn(keep.id, self.store._sessions)

    def test_idle_sessions_expire(self):
        """Ages the session explicitly rather than sleeping: the monotonic clock
        has ~15ms resolution on Windows, so a real sleep is not reliable here."""
        store = SessionStore(max_sessions=10, idle_ttl=30)
        old = store.get_or_create()
        old.last_used -= 31  # older than the TTL
        store.get_or_create()  # triggers the eviction sweep
        self.assertNotIn(old.id, store._sessions)

    def test_reset_clears_history_but_keeps_session(self):
        session = self.store.get_or_create()
        session.agent.history.append("something")
        self.assertTrue(self.store.reset(session.id))
        self.assertEqual(session.agent.history, [])
        self.assertIn(session.id, self.store._sessions)

    def test_reset_unknown_session_is_false(self):
        self.assertFalse(self.store.reset("nope"))

    def test_drop_removes_session(self):
        session = self.store.get_or_create()
        self.assertTrue(self.store.drop(session.id))
        self.assertFalse(self.store.drop(session.id))


class TestConfigValidation(unittest.TestCase):
    """validate() reads module-level constants, so each case patches and restores."""

    def _validate_with(self, **overrides):
        original = {k: getattr(config, k) for k in overrides}
        for key, value in overrides.items():
            setattr(config, key, value)
        try:
            return config.validate(require_model=False)
        finally:
            for key, value in original.items():
                setattr(config, key, value)

    def test_missing_api_key_is_fatal(self):
        original = config.API_KEYS
        config.API_KEYS = []
        try:
            with self.assertRaises(config.ConfigError):
                config.validate(require_model=True)
        finally:
            config.API_KEYS = original

    def test_dry_run_warns_but_does_not_fail(self):
        warnings = self._validate_with(EMAIL_ENABLED=False)
        self.assertTrue(any("dry-run" in w for w in warnings))

    def test_unknown_provider_is_fatal(self):
        with self.assertRaises(config.ConfigError):
            self._validate_with(EMAIL_ENABLED=True, EMAIL_PROVIDER="carrier-pigeon")

    def test_provider_without_its_key_is_fatal(self):
        with self.assertRaises(config.ConfigError) as ctx:
            self._validate_with(EMAIL_ENABLED=True, EMAIL_PROVIDER="resend",
                                RESEND_API_KEY="", EMAIL_FROM="a@b.com")
        self.assertIn("RESEND_API_KEY", str(ctx.exception))

    def test_enabled_without_sender_is_fatal(self):
        with self.assertRaises(config.ConfigError):
            self._validate_with(EMAIL_ENABLED=True, EMAIL_PROVIDER="smtp", EMAIL_FROM="")

    def test_summary_contains_no_secrets(self):
        blob = repr(config.summary())
        for secret in (config.SMTP_PASSWORD, *config.API_KEYS):
            if secret:
                self.assertNotIn(secret, blob)


class TestServerHelpers(unittest.TestCase):
    def setUp(self):
        import serving

        self.web = serving

    def test_rate_limiter_blocks_past_the_limit(self):
        limiter = self.web.RateLimiter(limit=3, window=60)
        self.assertTrue(all(limiter.allow("s") for _ in range(3)))
        self.assertFalse(limiter.allow("s"))
        self.assertTrue(limiter.allow("other"))  # limits are per key

    def test_rate_limiter_window_resets(self):
        """A window short enough to expire mid-test would be flaky under load,
        so expiry is simulated by rewinding the recorded start time."""
        limiter = self.web.RateLimiter(limit=1, window=30)
        self.assertTrue(limiter.allow("s"))
        self.assertFalse(limiter.allow("s"))
        start, count = limiter._hits["s"]
        limiter._hits["s"] = (start - 31, count)  # pretend the window elapsed
        self.assertTrue(limiter.allow("s"))

    def test_observable_trace_notifies_on_append(self):
        seen = []
        trace = self.web.ObservableTrace(seen.append)
        trace.append("one")
        trace.append("two")
        self.assertEqual(seen, ["one", "two"])
        self.assertEqual(list(trace), ["one", "two"])

    def test_observable_trace_survives_a_failing_listener(self):
        """A disconnected client must not break the agent turn."""
        def boom(_):
            raise RuntimeError("client gone")

        trace = self.web.ObservableTrace(boom)
        trace.append("still recorded")
        self.assertEqual(list(trace), ["still recorded"])

    def test_relative_traversal_is_neutralised_to_the_upload_dir(self):
        """A relative path keeps only its basename, so it cannot escape."""
        built = self.web.build_message("hello", ["../../etc/passwd"])
        self.assertIn("Attachment missing: passwd", built)
        self.assertNotIn("etc", built.replace("Attached files", ""))

    def test_absolute_path_outside_upload_dir_is_rejected(self):
        outside = str(Path(config.ROOT_DIR) / ".env")
        built = self.web.build_message("hello", [outside])
        self.assertIn("Rejected attachment outside the upload directory", built)
        self.assertNotIn("GOOGLE_API_KEY", built)



class TestAuth(unittest.TestCase):
    def setUp(self):
        from multi_agent_system import auth

        self.auth = auth

    def test_hash_is_salted_and_verifies(self):
        a = self.auth.hash_password("correct horse battery staple")
        b = self.auth.hash_password("correct horse battery staple")
        self.assertNotEqual(a, b, "each hash must use a fresh salt")
        self.assertTrue(self.auth.verify_password("correct horse battery staple", a))
        self.assertTrue(self.auth.verify_password("correct horse battery staple", b))

    def test_wrong_password_fails(self):
        stored = self.auth.hash_password("right")
        self.assertFalse(self.auth.verify_password("wrong", stored))
        self.assertFalse(self.auth.verify_password("", stored))

    def test_plaintext_password_is_never_stored(self):
        stored = self.auth.hash_password("s3cret-value")
        self.assertNotIn("s3cret-value", stored)

    def test_malformed_hash_is_rejected_not_crashing(self):
        for junk in ("", "nonsense", "bcrypt$a$b", "scrypt$only-two"):
            self.assertFalse(self.auth.verify_password("x", junk))

    def test_token_round_trip(self):
        token = self.auth.issue_token("admin", "secret-key", 3600)
        self.assertEqual(self.auth.read_token(token, "secret-key"), "admin")

    def test_token_rejected_under_a_different_key(self):
        token = self.auth.issue_token("admin", "key-one", 3600)
        self.assertIsNone(self.auth.read_token(token, "key-two"))

    def test_expired_token_is_rejected(self):
        token = self.auth.issue_token("admin", "secret-key", -1)
        self.assertIsNone(self.auth.read_token(token, "secret-key"))

    def test_tampered_token_is_rejected(self):
        token = self.auth.issue_token("admin", "secret-key", 3600)
        user, expiry, sig = token.rsplit("|", 2)
        # Privilege escalation attempt: swap the username, keep the signature.
        self.assertIsNone(self.auth.read_token(f"root|{expiry}|{sig}", "secret-key"))
        # Extend the lifetime, keep the signature.
        self.assertIsNone(self.auth.read_token(f"{user}|9999999999|{sig}", "secret-key"))

    def test_garbage_tokens_are_rejected(self):
        for junk in ("", "a", "a|b", "|||"):
            self.assertIsNone(self.auth.read_token(junk, "secret-key"))

    def test_limiter_blocks_then_expires(self):
        """Uses a long window and rewinds the clock, so a slow machine cannot
        let the window lapse mid-test and make this flaky."""
        limiter = self.auth.AttemptLimiter(limit=3, window=60)
        for _ in range(3):
            self.assertFalse(limiter.blocked("ip")[0])
            limiter.record_failure("ip")
        blocked, retry = limiter.blocked("ip")
        self.assertTrue(blocked)
        self.assertGreater(retry, 0)

        first, count = limiter._fails["ip"]
        limiter._fails["ip"] = (first - 61, count)  # pretend the window elapsed
        self.assertFalse(limiter.blocked("ip")[0], "lockout must lift after the window")

    def test_limiter_is_per_key(self):
        limiter = self.auth.AttemptLimiter(limit=1, window=60)
        limiter.record_failure("attacker")
        self.assertTrue(limiter.blocked("attacker")[0])
        self.assertFalse(limiter.blocked("innocent")[0])

    def test_success_clears_the_lockout_counter(self):
        limiter = self.auth.AttemptLimiter(limit=2, window=60)
        limiter.record_failure("ip")
        limiter.clear("ip")
        limiter.record_failure("ip")
        self.assertFalse(limiter.blocked("ip")[0])


class TestAuthConfig(unittest.TestCase):
    def _validate_with(self, **overrides):
        original = {k: getattr(config, k) for k in overrides}
        for key, value in overrides.items():
            setattr(config, key, value)
        try:
            return config.validate(require_model=False)
        finally:
            for key, value in original.items():
                setattr(config, key, value)

    def test_production_without_auth_refuses_to_start(self):
        with self.assertRaises(config.ConfigError) as ctx:
            self._validate_with(IS_PRODUCTION=True, AUTH_ENABLED=False)
        self.assertIn("AUTH_ENABLED=false in production", str(ctx.exception))

    def test_auth_without_a_password_is_fatal(self):
        with self.assertRaises(config.ConfigError):
            self._validate_with(AUTH_ENABLED=True, AUTH_PASSWORD="", AUTH_PASSWORD_HASH="")

    def test_production_without_secret_key_is_fatal(self):
        with self.assertRaises(config.ConfigError) as ctx:
            self._validate_with(IS_PRODUCTION=True, AUTH_ENABLED=True,
                                AUTH_PASSWORD_HASH="scrypt$a$b",
                                SECRET_KEY_GENERATED=True)
        self.assertIn("SECRET_KEY", str(ctx.exception))

    def test_auth_off_in_development_only_warns(self):
        warnings = self._validate_with(IS_PRODUCTION=False, AUTH_ENABLED=False)
        self.assertTrue(any("Authentication is off" in w for w in warnings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
