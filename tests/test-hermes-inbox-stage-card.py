#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_stage_card as stage_card


ACCOUNT = "zhachory1@gmail.com"
KEY = "inbox-message-" + hashlib.sha256(b"zhachory1@gmail.com\0source1").hexdigest()
HEADERS = [{"name": "From", "value": "Recruiter <recruiter@example.com>"},
           {"name": "Subject", "value": "Staff role"},
           {"name": "Message-ID", "value": "<source-1@example.com>"}]


class FakeGmail:
    def profile(self):
        return {"emailAddress": ACCOUNT}

    def get_thread(self, thread_id):
        assert thread_id == "thread1"
        return [{"id": "source1", "internalDate": "1760000000000", "headers": HEADERS,
                 "text": "Synthetic incoming request"}]


class FakeOps:
    def edit_task(self, conn, task_id, *, body):
        conn.execute("UPDATE tasks SET body = ? WHERE id = ?", (body, task_id))
        conn.execute("INSERT INTO task_events(task_id,kind) VALUES (?, 'edited')", (task_id,))
        conn.commit()
        return True

    def assign_task(self, conn, task_id, assignee):
        conn.execute("UPDATE tasks SET assignee = ? WHERE id = ?", (assignee, task_id))
        conn.execute("INSERT INTO task_events(task_id,kind) VALUES (?, 'assigned')", (task_id,))
        conn.commit()
        return True


class StageCardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.board = sqlite3.connect(":memory:"); self.addCleanup(self.board.close)
        self.board.row_factory = sqlite3.Row
        intake = {"account": ACCOUNT, "message_id": "source1", "thread_id": "thread1",
                  "source_day": "2026-10-07", "route": "job"}
        self.board.executescript("""
            CREATE TABLE tasks (id TEXT PRIMARY KEY, body TEXT, status TEXT, assignee TEXT,
                                claim_lock TEXT, created_by TEXT, idempotency_key TEXT);
            CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER);
            CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at INTEGER);
        """)
        self.board.execute("INSERT INTO tasks VALUES (?,?, 'blocked','inbox-sol',NULL,'inbox-intake',?)",
                           ("t_12345678", json.dumps(intake), KEY))
        self.board.commit()
        self.drafts = pathlib.Path(self.temp.name) / "drafts.sqlite"

    def test_stages_preview_and_unassigns_without_unblocking(self):
        result = stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(),
                                  "Thanks. What is the team scope?", ops=FakeOps())
        task = self.board.execute("SELECT body,status,assignee FROM tasks").fetchone()
        self.assertEqual((task["status"], task["assignee"]), ("blocked", None))
        preview = json.loads(task["body"])
        self.assertEqual(preview["to"], "recruiter@example.com")
        self.assertEqual(preview["draft"], "Thanks. What is the team scope?")
        self.assertEqual(preview["source_day"], "2026-10-07")
        self.assertNotIn("Synthetic incoming request", task["body"])
        self.assertEqual(result["version"], 1)
        self.assertEqual(self.board.execute("SELECT kind FROM task_events ORDER BY id DESC LIMIT 1").fetchone()[0],
                         "inbox_staged")
        self.assertEqual(stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(),
                                          "Thanks. What is the team scope?", ops=FakeOps())["digest"], result["digest"])

    def test_edited_review_card_is_not_silently_overwritten(self):
        stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(), "Draft", ops=FakeOps())
        card = json.loads(self.board.execute("SELECT body FROM tasks").fetchone()[0])
        card["to"] = "attacker@example.com"
        self.board.execute("UPDATE tasks SET body = ?", (json.dumps(card),))
        self.board.commit()
        with self.assertRaises(ValueError):
            stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(), "Draft", ops=FakeOps())
        self.assertEqual(json.loads(self.board.execute("SELECT body FROM tasks").fetchone()[0])["to"],
                         "attacker@example.com")

    def test_explicit_restaging_invalidates_old_cue(self):
        first = stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(), "Draft", ops=FakeOps())
        card = json.loads(self.board.execute("SELECT body FROM tasks").fetchone()[0])
        card["draft"] = "Revised draft"
        self.board.execute("UPDATE tasks SET body = ?", (json.dumps(card),))
        self.board.execute("INSERT INTO task_events(task_id,kind) VALUES ('t_12345678','edited')")
        self.board.commit()
        with self.assertRaises(ValueError):
            stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(), "Revised draft", ops=FakeOps())
        second = stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(), "Revised draft",
                                  ops=FakeOps(), restage=True)
        self.assertEqual(second["version"], 2)
        self.assertNotEqual(first["digest"], second["digest"])
        self.assertGreater(second["stage_event"], first["stage_event"])
        self.assertEqual(stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(),
                                          "Revised draft", ops=FakeOps(), restage=True)["version"], 2)

    def test_restarts_after_preview_write_before_unassign(self):
        class FailAssign(FakeOps):
            def assign_task(self, conn, task_id, assignee):
                raise RuntimeError("synthetic crash")
        with self.assertRaises(RuntimeError):
            stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(), "Draft", ops=FailAssign())
        self.assertEqual(self.board.execute("SELECT assignee FROM tasks").fetchone()[0], "inbox-sol")
        resumed = stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(), "Draft", ops=FakeOps())
        self.assertEqual(resumed["version"], 1)
        self.assertIsNone(self.board.execute("SELECT assignee FROM tasks").fetchone()[0])

    def test_card_edit_during_thread_read_is_not_overwritten(self):
        board = self.board
        class ChangedDuringRead(FakeGmail):
            def get_thread(self, thread_id):
                board.execute("UPDATE tasks SET body = 'human edited intake card' WHERE id = 't_12345678'")
                board.commit()
                return super().get_thread(thread_id)
        with self.assertRaises(ValueError):
            stage_card.stage(self.board, self.drafts, "t_12345678", ChangedDuringRead(),
                             "Draft", ops=FakeOps())
        task = self.board.execute("SELECT body,assignee FROM tasks").fetchone()
        self.assertEqual((task["body"], task["assignee"]), ("human edited intake card", "inbox-sol"))
        self.assertIsNone(self.board.execute("SELECT 1 FROM task_events WHERE kind='inbox_staged'").fetchone())

    def test_incorrect_card_identity_never_reads_thread(self):
        self.board.execute("UPDATE tasks SET idempotency_key = 'wrong'")
        self.board.commit()
        with self.assertRaises(ValueError):
            stage_card.stage(self.board, self.drafts, "t_12345678", FakeGmail(), "Draft", ops=FakeOps())
        self.assertFalse(self.drafts.exists())


if __name__ == "__main__":
    unittest.main()
