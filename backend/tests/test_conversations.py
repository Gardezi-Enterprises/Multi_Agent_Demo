"""Tests for per-user conversation storage. No model calls, no network."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import multi_agent_system.config as config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="mas_conv_"))
config.DB_PATH = _TMP / "conv.db"

import multi_agent_system.db.database as database  # noqa: E402

database.DB_PATH = config.DB_PATH

from multi_agent_system import conversations as cv  # noqa: E402


class TestConversations(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        database.init_db(force=True)

    def test_create_and_list_is_owner_scoped(self):
        cv.create("alice", "Alice chat")
        cv.create("bob", "Bob chat")
        self.assertEqual([c["title"] for c in cv.list_for("alice")], ["Alice chat"])
        self.assertEqual([c["title"] for c in cv.list_for("bob")], ["Bob chat"])

    def test_owner_check_is_case_insensitive_for_the_owner_only(self):
        c = cv.create("Alice", "Hi")["id"]
        self.assertTrue(cv.owns("alice", c))
        self.assertFalse(cv.owns("bob", c))

    def test_other_user_cannot_read_messages(self):
        c = cv.create("alice", "Hi")["id"]
        cv.add_message(c, "user", "hello")
        cv.add_message(c, "assistant", "hi back")
        self.assertEqual(len(cv.get_messages("alice", c)), 2)
        # The core guarantee: a different owner gets nothing, not an error leak.
        self.assertIsNone(cv.get_messages("bob", c))

    def test_messages_round_trip_with_trace_and_downloads(self):
        c = cv.create("alice")["id"]
        cv.add_message(c, "user", "build a resume")
        cv.add_message(c, "assistant", "done",
                       trace=[{"agent": "Builder", "status": "success"}],
                       downloads=[{"name": "x_resume.docx", "download_name": "resume.docx", "size": "36 KB"}])
        msgs = cv.get_messages("alice", c)
        self.assertEqual(msgs[1]["trace"][0]["agent"], "Builder")
        self.assertEqual(msgs[1]["downloads"][0]["download_name"], "resume.docx")

    def test_title_if_default_only_sets_once(self):
        c = cv.create("alice")["id"]  # title defaults to "New chat"
        cv.title_if_default("alice", c, "first message text")
        self.assertEqual(cv.list_for("alice")[0]["title"], "first message text")
        cv.title_if_default("alice", c, "second message text")  # no-op now
        self.assertEqual(cv.list_for("alice")[0]["title"], "first message text")

    def test_delete_is_owner_scoped(self):
        c = cv.create("alice", "Hi")["id"]
        self.assertFalse(cv.delete("bob", c))  # a stranger cannot delete it
        self.assertTrue(cv.owns("alice", c))
        self.assertTrue(cv.delete("alice", c))
        self.assertEqual(cv.list_for("alice"), [])

    def test_clear_all_only_affects_the_owner(self):
        cv.create("alice", "a1")
        cv.create("alice", "a2")
        cv.create("bob", "b1")
        self.assertEqual(cv.clear_all("alice"), 2)
        self.assertEqual(cv.list_for("alice"), [])
        self.assertEqual(len(cv.list_for("bob")), 1)

    def test_deleting_a_conversation_removes_its_messages(self):
        c = cv.create("alice")["id"]
        cv.add_message(c, "user", "x")
        cv.delete("alice", c)
        with database.get_connection() as conn:
            left = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (c,)
            ).fetchone()[0]
        self.assertEqual(left, 0)

    def test_build_history_alternates_roles(self):
        history = cv.build_history([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
        ])
        self.assertEqual([h.role for h in history], ["user", "model", "user"])
        self.assertEqual(history[0].parts[0].text, "hi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
