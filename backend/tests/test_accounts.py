"""Tests for the operator account store. No model calls, no network."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import multi_agent_system.config as config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="mas_acct_"))
config.DB_PATH = _TMP / "accts.db"

import multi_agent_system.db.database as database  # noqa: E402

database.DB_PATH = config.DB_PATH

from multi_agent_system import accounts, auth  # noqa: E402


class TestAccounts(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        database.init_db(force=True)

    def test_first_account_and_verification(self):
        self.assertEqual(accounts.count(), 0)
        result = accounts.create("owner", "s3cret-pass", is_admin=True)
        self.assertEqual(result["status"], "success")
        self.assertEqual(accounts.count(), 1)
        self.assertIsNotNone(accounts.verify("owner", "s3cret-pass"))
        self.assertIsNone(accounts.verify("owner", "wrong-pass"))
        self.assertIsNone(accounts.verify("ghost", "s3cret-pass"))

    def test_password_is_hashed_not_stored(self):
        accounts.create("owner", "plaintext-secret")
        row = accounts.get("owner")
        self.assertNotIn("plaintext-secret", row["password_hash"])
        self.assertTrue(row["password_hash"].startswith("scrypt$"))

    def test_username_is_case_insensitive_and_unique(self):
        accounts.create("Owner", "password-one")
        dup = accounts.create("owner", "password-two")
        self.assertEqual(dup["status"], "error")
        self.assertIsNotNone(accounts.verify("OWNER", "password-one"))

    def test_validation(self):
        self.assertEqual(accounts.create("ab", "longenough1")["status"], "error")  # short user
        self.assertEqual(accounts.create("okname", "short")["status"], "error")  # short pass
        self.assertEqual(accounts.create("bad name!", "longenough1")["status"], "error")

    def test_set_password_changes_token_salt(self):
        accounts.create("owner", "first-password")
        before = accounts.token_salt(accounts.get("owner"))
        accounts.set_password("owner", "second-password")
        after = accounts.token_salt(accounts.get("owner"))
        self.assertNotEqual(before, after, "token salt must change so old tokens die")
        self.assertIsNotNone(accounts.verify("owner", "second-password"))
        self.assertIsNone(accounts.verify("owner", "first-password"))

    def test_token_survives_same_password_but_not_a_change(self):
        accounts.create("owner", "the-password")
        acct = accounts.get("owner")
        token = auth.issue_token("owner", "secret", 3600, salt=accounts.token_salt(acct))
        self.assertEqual(
            auth.read_token(token, "secret", salt=accounts.token_salt(acct)), "owner"
        )
        accounts.set_password("owner", "new-password")
        fresh = accounts.get("owner")
        # The same token, checked against the new salt, must now fail.
        self.assertIsNone(auth.read_token(token, "secret", salt=accounts.token_salt(fresh)))

    def test_delete_and_last_admin_protection(self):
        accounts.create("admin1", "password-aaa", is_admin=True)
        accounts.create("member", "password-bbb", is_admin=False)
        self.assertEqual(accounts.delete("member")["status"], "success")
        # admin1 is the only admin left; deletion must be refused.
        self.assertEqual(accounts.delete("admin1")["status"], "error")
        accounts.create("admin2", "password-ccc", is_admin=True)
        self.assertEqual(accounts.delete("admin1")["status"], "success")

    def test_seed_from_env_only_when_empty(self):
        h = auth.hash_password("env-password")
        accounts.ensure_seed_from_env("seeded", h, "")
        self.assertEqual(accounts.count(), 1)
        self.assertTrue(accounts.get("seeded")["is_admin"])
        # A second call is a no-op because the table is no longer empty.
        accounts.ensure_seed_from_env("other", h, "")
        self.assertEqual(accounts.count(), 1)
        self.assertIsNone(accounts.get("other"))



    def test_email_lookup_and_uniqueness(self):
        accounts.create("owner", "owner-password-1", email="owner@example.com")
        self.assertIsNotNone(accounts.get_by_email("owner@example.com"))
        self.assertIsNotNone(accounts.get_by_email("OWNER@EXAMPLE.COM"))  # case-insensitive
        dup = accounts.create("other", "other-password-1", email="owner@example.com")
        self.assertEqual(dup["status"], "error")

    def test_invalid_email_rejected(self):
        self.assertEqual(
            accounts.create("owner", "owner-password-1", email="not-an-email")["status"], "error"
        )

    def test_set_email(self):
        accounts.create("owner", "owner-password-1")
        self.assertIsNone(accounts.get("owner")["email"])
        self.assertEqual(accounts.set_email("owner", "new@example.com")["status"], "success")
        self.assertEqual(accounts.get("owner")["email"], "new@example.com")

    def test_reset_token_is_single_use_and_namespaced(self):
        accounts.create("owner", "owner-password-1", email="owner@example.com")
        acct = accounts.get("owner")
        salt = accounts.token_salt(acct)
        token = auth.issue_reset_token("owner", "secret", 1800, salt)
        # A valid reset token verifies as a reset token...
        self.assertEqual(auth.read_reset_token(token, "secret", salt), "owner")
        # ...but NOT as a login/session token (different purpose namespace).
        self.assertIsNone(auth.read_token(token, "secret", salt=salt))
        # After the password changes, the salt rotates and the token dies.
        accounts.set_password("owner", "new-password-99")
        fresh = accounts.token_salt(accounts.get("owner"))
        self.assertIsNone(auth.read_reset_token(token, "secret", fresh))

    def test_expired_reset_token_rejected(self):
        accounts.create("owner", "owner-password-1", email="owner@example.com")
        salt = accounts.token_salt(accounts.get("owner"))
        token = auth.issue_reset_token("owner", "secret", -1, salt)
        self.assertIsNone(auth.read_reset_token(token, "secret", salt))


if __name__ == "__main__":
    unittest.main(verbosity=2)
