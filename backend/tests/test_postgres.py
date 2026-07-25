"""PostgreSQL integration test.

Skipped unless TEST_DATABASE_URL points at a Postgres database, e.g.:

    docker run -d --name pg -e POSTGRES_PASSWORD=testpass -p 55432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:testpass@127.0.0.1:55432/postgres \\
        python tests/test_postgres.py

Verifies the same store code runs correctly on Postgres, including the
case-insensitive account lookups and per-user conversation isolation.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_PG_URL = os.getenv("TEST_DATABASE_URL", "")


@unittest.skipUnless(
    _PG_URL.startswith(("postgres://", "postgresql://")),
    "set TEST_DATABASE_URL to a postgres:// URL to run",
)
class TestPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import multi_agent_system.config as config

        config.DATABASE_URL = _PG_URL
        config.IS_POSTGRES = True
        config.DB_PATH = None

        import multi_agent_system.db.database as database

        # Rebind the module-level constants the layer reads.
        database.DATABASE_URL = _PG_URL
        database.IS_POSTGRES = True
        cls.db = database
        with database.get_connection() as conn:
            for table in ("messages", "conversations", "auth_accounts", "users"):
                conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        database._initialised = False
        database.init_db(force=True)

    def test_user_tools(self):
        from multi_agent_system.tools.db_tools import create_user, edit_user, get_all_users

        self.assertEqual(create_user("Ada", "ada@pg.com", department="Data")["status"], "success")
        self.assertEqual(create_user("Dup", "ada@pg.com")["status"], "error")  # unique email
        self.assertGreaterEqual(get_all_users()["count"], 1)
        self.assertEqual(edit_user(email="ada@pg.com", new_phone="555-0000")["user"]["phone"], "555-0000")

    def test_accounts_case_insensitive(self):
        from multi_agent_system import accounts

        accounts.create("PgOwner", "owner-pass-1", is_admin=True, email="owner@pg.com")
        self.assertEqual(accounts.get("pgowner")["username"], "PgOwner")  # case-insensitive
        self.assertIsNotNone(accounts.get_by_email("OWNER@PG.COM"))
        self.assertEqual(accounts.create("pgowner", "x-pass-123")["status"], "error")  # dup
        self.assertIsNotNone(accounts.verify("PGOWNER", "owner-pass-1"))
        self.assertIsNone(accounts.verify("pgowner", "wrong"))

    def test_conversation_isolation(self):
        from multi_agent_system import conversations as cv

        c = cv.create("alice", "Alice chat")["id"]
        cv.add_message(c, "user", "secret")
        cv.add_message(c, "assistant", "ok", trace=[{"a": 1}], downloads=[])
        self.assertEqual(len(cv.get_messages("alice", c)), 2)
        self.assertIsNone(cv.get_messages("bob", c))  # the guarantee
        self.assertFalse(cv.owns("bob", c))
        self.assertFalse(cv.delete("bob", c))
        self.assertTrue(cv.delete("alice", c))


if __name__ == "__main__":
    unittest.main(verbosity=2)
