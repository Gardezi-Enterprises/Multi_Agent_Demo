"""Tests for the tool layer.

These exercise every tool directly, with no model calls, so the whole tool
surface can be verified without an API key or network access.

Run with:  python tests/test_tools.py     (or: pytest tests/)
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point the database and output at a temp directory before importing the tools.
import multi_agent_system.config as config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="mas_tests_"))
config.DB_PATH = _TMP / "test.db"
config.OUTPUT_DIR = _TMP

import multi_agent_system.db.database as database  # noqa: E402

database.DB_PATH = config.DB_PATH

import multi_agent_system.tools.docgen_tools as docgen  # noqa: E402
import multi_agent_system.tools.email_tools as email_tools  # noqa: E402
from multi_agent_system.tools.analysis_tools import analyze_resume_text  # noqa: E402
from multi_agent_system.tools.db_tools import create_user, edit_user, get_all_users  # noqa: E402
from multi_agent_system import commands  # noqa: E402
from multi_agent_system.documents import extract_text  # noqa: E402

docgen.OUTPUT_DIR = _TMP
email_tools.EMAIL_LOG = _TMP / "sent_emails.log"
# Force dry-run regardless of the developer's .env: the suite must never send
# real mail, and must not depend on ambient SMTP configuration.
email_tools.EMAIL_ENABLED = False

RESUME = """Priya Sharma
priya.sharma@example.com | +91 98765 43210

Senior Data Scientist with 8 years of experience building ML systems.

SKILLS
Python, TensorFlow, PyTorch, scikit-learn, Pandas, NLP, SQL, Statistics

EDUCATION
M.Tech in Artificial Intelligence, IIT Bombay
"""


class TestUserTools(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        # force=True because init_db memoises: the file was just deleted.
        database.init_db(force=True)

    def test_create_user_succeeds(self):
        result = create_user("Ada Lovelace", "ada@example.com", department="Software Engineering")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["user"]["name"], "Ada Lovelace")
        self.assertIsNotNone(result["user"]["id"])

    def test_create_user_rejects_invalid_email(self):
        result = create_user("Bad Email", "not-an-email")
        self.assertEqual(result["status"], "error")

    def test_create_user_rejects_blank_name(self):
        result = create_user("   ", "blank@example.com")
        self.assertEqual(result["status"], "error")

    def test_create_user_rejects_duplicate_email(self):
        create_user("First", "dupe@example.com")
        result = create_user("Second", "dupe@example.com")
        self.assertEqual(result["status"], "error")
        self.assertIn("already exists", result["message"])

    def test_get_all_users_counts_and_limits(self):
        create_user("One", "one@example.com")
        create_user("Two", "two@example.com")
        self.assertEqual(get_all_users()["count"], 2)
        self.assertEqual(get_all_users(limit=1)["count"], 1)

    def test_edit_user_by_email_and_id(self):
        created = create_user("Grace Hopper", "grace@example.com")
        by_email = edit_user(email="grace@example.com", new_department="Engineering")
        self.assertEqual(by_email["status"], "success")
        self.assertEqual(by_email["user"]["department"], "Engineering")

        by_id = edit_user(user_id=created["user"]["id"], new_phone="555-123-4567")
        self.assertEqual(by_id["user"]["phone"], "555-123-4567")
        self.assertEqual(by_id["user"]["name"], "Grace Hopper")  # untouched

    def test_edit_user_errors(self):
        self.assertEqual(edit_user()["status"], "error")  # no identifier
        self.assertEqual(edit_user(user_id=999, new_name="Ghost")["status"], "error")
        create_user("Real", "real@example.com")
        self.assertEqual(edit_user(email="real@example.com")["status"], "error")  # no changes

    def test_edit_user_rejects_email_collision(self):
        create_user("A", "a@example.com")
        create_user("B", "b@example.com")
        result = edit_user(email="b@example.com", new_email="a@example.com")
        self.assertEqual(result["status"], "error")
        self.assertIn("already used", result["message"])


class TestEmailTool(unittest.TestCase):
    def test_dry_run_send(self):
        result = email_tools.send_email("bob@example.com", "Hello", "Body text")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["mode"], "dry_run")
        self.assertIn("NOT delivered", result["message"])

    def test_attachment_missing_is_reported(self):
        result = email_tools.send_email(
            "a@b.com", "s", "b", attachments=[str(_TMP / "no_such_file.docx")]
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("not found", result["message"].lower())

    def test_attachment_is_accepted(self):
        path = _TMP / "attach.txt"
        path.write_text("data", encoding="utf-8")
        result = email_tools.send_email("a@b.com", "s", "b", attachments=[str(path)])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["attachments"], ["attach.txt"])

    def test_multiple_recipients(self):
        result = email_tools.send_email("a@b.com, c@d.com", "s", "b")
        self.assertEqual(result["status"], "success")
        self.assertIn("c@d.com", result["recipients"])
        self.assertEqual(result["recipient_count"], 2)
        self.assertFalse(result["sent_individually"])

    def test_broadcast_sends_individually(self):
        result = email_tools.send_email(
            "a@b.com, c@d.com, e@f.com", "Notice", "Body", send_individually=True
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["recipient_count"], 3)
        self.assertTrue(result["sent_individually"])

    def test_duplicate_recipients_are_collapsed(self):
        result = email_tools.send_email("a@b.com, A@B.com, c@d.com", "s", "b")
        self.assertEqual(result["recipient_count"], 2)

    def test_one_invalid_address_rejects_the_whole_send(self):
        result = email_tools.send_email("a@b.com, notanemail", "s", "b")
        self.assertEqual(result["status"], "error")

    def test_recipient_limit_is_enforced(self):
        many = ", ".join(f"u{i}@x.com" for i in range(email_tools.MAX_RECIPIENTS + 1))
        result = email_tools.send_email(many, "s", "b")
        self.assertEqual(result["status"], "error")
        self.assertIn("limit", result["message"])

    def test_partial_failure_is_reported_as_partial(self):
        """A send that reaches some recipients must never report clean success."""
        original_enabled = email_tools.EMAIL_ENABLED
        original_deliver = email_tools.deliver
        email_tools.EMAIL_ENABLED = True
        email_tools.deliver = lambda rcpts, *a, **k: (
            (False, "mailbox full") if any("b@y.com" in r for r in rcpts)
            else (True, "HTTP 200")
        )
        try:
            result = email_tools.send_email(
                "a@x.com, b@y.com, c@z.com", "s", "b", send_individually=True
            )
        finally:
            email_tools.EMAIL_ENABLED = original_enabled
            email_tools.deliver = original_deliver

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["delivered_count"], 2)
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("b@y.com", result["message"])

    def test_total_failure_is_an_error(self):
        original_enabled = email_tools.EMAIL_ENABLED
        original_deliver = email_tools.deliver
        email_tools.EMAIL_ENABLED = True
        email_tools.deliver = lambda *a, **k: (False, "auth rejected")
        try:
            result = email_tools.send_email("a@x.com", "s", "b")
        finally:
            email_tools.EMAIL_ENABLED = original_enabled
            email_tools.deliver = original_deliver
        self.assertEqual(result["status"], "error")
        self.assertIn("auth rejected", result["message"])

    def test_signature_is_appended_only_when_given(self):
        email_tools.send_email("a@b.com", "s", "Body", signature="The Team")
        logged = email_tools.EMAIL_LOG.read_text(encoding="utf-8")
        self.assertIn("The Team", logged)

    def test_validation(self):
        self.assertEqual(email_tools.send_email("bad", "s", "b")["status"], "error")
        self.assertEqual(email_tools.send_email("a@b.com", "", "b")["status"], "error")
        self.assertEqual(email_tools.send_email("a@b.com", "s", "  ")["status"], "error")


class TestAnalysisTool(unittest.TestCase):
    """The analyser delegates extraction to the model, so only its input
    validation and contact-detail parsing are testable offline."""

    def test_rejects_short_text(self):
        self.assertEqual(analyze_resume_text("too short")["status"], "error")

    def test_rejects_empty_text(self):
        self.assertEqual(analyze_resume_text("")["status"], "error")

    def test_phone_extraction_formats(self):
        from multi_agent_system.tools.analysis_tools import _extract_phone

        self.assertEqual(_extract_phone("+91 98765 43210"), "+91 98765 43210")
        self.assertEqual(_extract_phone("(555) 123-4567"), "(555) 123-4567")
        self.assertEqual(_extract_phone("555-123-4567"), "555-123-4567")

    def test_phone_extraction_ignores_date_ranges(self):
        from multi_agent_system.tools.analysis_tools import _extract_phone

        self.assertEqual(_extract_phone("Worked 2020-2024 at Acme"), "")


class TestDocumentExtraction(unittest.TestCase):
    def test_reads_plain_text(self):
        path = _TMP / "sample.txt"
        path.write_text("Hello resume", encoding="utf-8")
        text, error = extract_text(str(path))
        self.assertEqual(error, "")
        self.assertIn("Hello resume", text)

    def test_reads_docx_roundtrip(self):
        result = docgen.generate_resume_document("Round Trip", skills="Python, SQL")
        text, error = extract_text(result["file_path"])
        self.assertEqual(error, "")
        self.assertIn("ROUND TRIP", text.upper())

    def test_missing_file_reports_error(self):
        text, error = extract_text(str(_TMP / "nope.txt"))
        self.assertEqual(text, "")
        self.assertIn("not found", error.lower())

    def test_unsupported_suffix_reports_error(self):
        path = _TMP / "thing.xyz"
        path.write_bytes(bytes([0, 1, 2]))
        text, error = extract_text(str(path))
        self.assertEqual(text, "")
        self.assertTrue(error)


class TestSlashCommands(unittest.TestCase):
    def test_plain_text_passes_through(self):
        self.assertEqual(commands.expand("list all users"), "list all users")

    def test_command_expands_with_args(self):
        out = commands.expand("/email bob@x.com say hello")
        self.assertIn("bob@x.com say hello", out)
        self.assertIn("Send an email", out)

    def test_argless_command_expands(self):
        self.assertIn("List all users", commands.expand("/users"))

    def test_missing_args_asks_instead_of_running(self):
        out = commands.expand("/email")
        self.assertIn("usage", out.lower())
        self.assertIn("Do not call any tools", out)

    def test_unknown_command_is_reported(self):
        out = commands.expand("/nonsense")
        self.assertIn("unrecognised", out.lower())

    def test_every_command_has_metadata(self):
        for entry in commands.as_json():
            self.assertTrue(entry["name"] and entry["summary"] and entry["usage"])


class TestDocGenTool(unittest.TestCase):
    def test_generates_docx(self):
        result = docgen.generate_resume_document(
            "Ravi Kumar",
            email="ravi@example.com",
            job_title="DevOps Engineer",
            summary="Experienced engineer.",
            skills="Python, AWS, Docker",
            experience=["Senior Engineer | Acme | 2020-2024 | Cut deploy time 60%; Led a team of 5"],
            education=["B.Tech CSE | IIT Delhi | 2017"],
            certifications="AWS Solutions Architect",
        )
        self.assertEqual(result["status"], "success")
        path = Path(result["file_path"])
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 0)
        self.assertIn("experience", result["sections_included"])

    def test_generates_txt_with_content(self):
        result = docgen.generate_resume_document(
            "Txt Person", skills="Python, SQL", file_format="txt"
        )
        self.assertEqual(result["status"], "success")
        text = Path(result["file_path"]).read_text(encoding="utf-8")
        self.assertIn("TXT PERSON", text)
        self.assertIn("Python", text)

    def test_additional_sections_are_generic(self):
        result = docgen.generate_resume_document(
            "Dr Maya Rao",
            file_format="txt",
            additional_sections=["Publications | Paper A; Paper B",
                                 "Languages | English; Hindi"],
        )
        self.assertEqual(result["status"], "success")
        text = Path(result["file_path"]).read_text(encoding="utf-8")
        self.assertIn("PUBLICATIONS", text)
        self.assertIn("Paper A", text)
        self.assertIn("English", text)
        self.assertIn("publications", result["sections_included"])

    def test_default_download_name_is_resume(self):
        result = docgen.generate_resume_document("Grace Hopper", skills="COBOL")
        self.assertEqual(result["download_name"], "resume.docx")
        # Stored uniquely on disk so files don't collide across candidates.
        self.assertIn("grace_hopper", result["file_name"])

    def test_custom_download_name_is_honoured_and_sanitised(self):
        result = docgen.generate_resume_document(
            "Grace Hopper", skills="COBOL", file_name="Grace CV"
        )
        self.assertEqual(result["download_name"], "Grace_CV.docx")

    def test_custom_name_keeps_a_supplied_extension(self):
        result = docgen.generate_resume_document(
            "Grace Hopper", skills="COBOL", file_name="cv.docx"
        )
        self.assertEqual(result["download_name"], "cv.docx")

    def test_validation(self):
        self.assertEqual(docgen.generate_resume_document("")["status"], "error")
        self.assertEqual(docgen.generate_resume_document("X", file_format="pdf")["status"], "error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
