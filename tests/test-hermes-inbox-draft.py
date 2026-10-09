#!/usr/bin/env python3
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_draft as draft


HEADERS = [{"name": "From", "value": "Recruiter <recruiter@example.com>"},
           {"name": "Reply-To", "value": "replies@example.com"},
           {"name": "Subject", "value": "Staff role"},
           {"name": "Message-ID", "value": "<source-1@example.com>"}]
THREAD = [{"id": "source-1", "internalDate": "1760000000000", "headers": HEADERS, "text": "Synthetic request"}]


class DraftStageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = pathlib.Path(self.temp.name) / "drafts.sqlite"

    def stage(self, body="Thanks. Could you share the role scope?", thread=THREAD,
              authenticated="zhachory1@gmail.com", restage=False):
        class Gmail:
            def profile(self):
                return {"emailAddress": authenticated}
        return draft.stage(self.db, "t_12345678", Gmail(), "zhachory1@gmail.com", "thread-1",
                           thread, body, restage=restage)

    def test_exact_mime_digest_stable_on_retry(self):
        first = self.stage()
        second = self.stage()
        self.assertEqual(first, second)
        self.assertEqual(first["recipient"], "replies@example.com")
        self.assertEqual(first["source_id"], "source-1")
        self.assertIn(b"To: replies@example.com\r\n", first["mime"])
        self.assertIn(b"In-Reply-To: <source-1@example.com>\r\n", first["mime"])
        self.assertIn(b"Thanks. Could you share the role scope?", first["mime"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0], 1)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_authenticated_account_and_latest_message_bind_draft(self):
        with self.assertRaises(ValueError):
            self.stage(authenticated="other@example.com")
        earlier = {**THREAD[0], "id": "older", "internalDate": "1759999999999",
                   "headers": [{"name": "From", "value": "older@example.com"}, *HEADERS[1:]]}
        staged = self.stage(thread=[THREAD[0], earlier])
        self.assertEqual(staged["source_id"], "source-1")
        self.assertEqual(staged["recipient"], "replies@example.com")

    def test_changed_draft_requires_explicit_restage(self):
        self.stage()
        with self.assertRaises(ValueError):
            self.stage("Different reply")

    def test_explicit_restage_changes_version_and_mime_once(self):
        first = self.stage()
        second = self.stage("Revised reply", restage=True)
        self.assertEqual(second["version"], 2)
        self.assertNotEqual(first["digest"], second["digest"])
        self.assertNotEqual(first["mime"], second["mime"])
        self.assertEqual(self.stage("Revised reply", restage=True), second)

    def test_in_flight_attempt_prevents_restage(self):
        first = self.stage()
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO gmail_drafts(task_id,version,digest,status) "
                         "VALUES ('t_12345678',1,?,'started')", (first["digest"],))
        with self.assertRaises(ValueError):
            self.stage("New text", restage=True)
        self.assertEqual(self.stage()["version"], 1)

    def test_ambiguous_recipient_and_injected_headers_fail_closed(self):
        for headers in ([*HEADERS, {"name": "Reply-To", "value": "other@example.com"}],
                        [{"name": "From", "value": "a@example.com, b@example.com"}, *HEADERS[2:]],
                        [{"name": "From", "value": "attacker@example.com\nBcc: attacker@example.com"}, *HEADERS[2:]],
                        [{"name": "From", "value": "zhachory1@gmail.com"}, *HEADERS[2:]]):
            with self.assertRaises(ValueError):
                self.stage(thread=[{**THREAD[0], "headers": headers}])
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
