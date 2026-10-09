#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sqlite3
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_board_cue as cue


class BoardCueTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE tasks (id TEXT PRIMARY KEY, body TEXT, status TEXT, assignee TEXT, claim_lock TEXT,
                                created_by TEXT, idempotency_key TEXT);
            CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER);
            INSERT INTO tasks VALUES ('t_1234', 'draft', 'blocked', NULL, NULL,
                                      'inbox-intake', 'inbox-message-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
        """)
        self.digest = hashlib.sha256(b"draft").hexdigest()

    def test_stages_only_unchanged_blocked_inbox_card(self):
        event = cue.stage(self.conn, "t_1234", self.digest)
        payload = self.conn.execute("SELECT kind,payload FROM task_events WHERE id = ?", (event,)).fetchone()
        self.assertEqual(payload["kind"], "inbox_staged")
        self.assertEqual(json.loads(payload["payload"]), {"body_sha256": self.digest})
        with self.assertRaises(ValueError):
            cue.stage(self.conn, "t_1234", hashlib.sha256(b"edited").hexdigest())
        self.conn.execute("UPDATE tasks SET status='ready' WHERE id='t_1234'")
        with self.assertRaises(ValueError):
            cue.stage(self.conn, "t_1234", self.digest)
        self.conn.execute("UPDATE tasks SET status='blocked', created_by='other' WHERE id='t_1234'")
        with self.assertRaises(ValueError):
            cue.stage(self.conn, "t_1234", self.digest)


if __name__ == "__main__":
    unittest.main()
