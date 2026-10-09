#!/usr/bin/env python3
from datetime import date, datetime, timezone
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_daily as daily
import hermes_inbox_reader as reader


NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


class FakeGmail:
    def __init__(self, ids=("one",)):
        self.ids = ids
        self.calls = 0

    def profile(self):
        self.calls += 1
        return {"emailAddress": "zhachory1@gmail.com"}

    def list_messages(self, query, *, label_ids, page_token=None):
        return {"messages": [{"id": mid} for mid in self.ids]}

    def get_message(self, message_id, *, format, metadata_headers):
        return {"id": message_id, "threadId": "thread-" + message_id,
                "internalDate": str(reader.previous_day(NOW)[1]), "labelIds": ["INBOX"]}


class DailyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = pathlib.Path(self.temp.name) / "inbox.sqlite"
        self.notices = []
        self.admitted = []

    def run_day(self, gmail, now=NOW, since=date(2026, 10, 7), admit=None):
        if admit is None:
            def admit(day, message, key):
                self.admitted.append((message["id"], key))
                return "admitted"
        return daily.run(self.db, gmail, now, "zhachory1@gmail.com", since,
                         lambda day, reason: self.notices.append((day, reason)), admit)

    def test_complete_once_and_replay_does_not_read(self):
        gmail = FakeGmail()
        self.assertEqual(self.run_day(gmail), 1)
        self.assertEqual(self.run_day(gmail), 0)
        self.assertEqual(gmail.calls, 1)
        self.assertEqual([mid for mid, _ in self.admitted], ["one"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status, count FROM days WHERE day='2026-10-07'").fetchone(),
                             ("complete", 1))
        self.assertEqual(self.notices, [])

    def test_lock_does_not_create_file_in_untrusted_directory(self):
        root = pathlib.Path(self.temp.name)
        root.chmod(0o755)
        try:
            with self.assertRaises(ValueError):
                with daily.lock_manifest(self.db):
                    pass
            self.assertFalse(pathlib.Path(f"{self.db}.lock").exists())
        finally:
            root.chmod(0o700)

    def test_manifest_is_bound_to_one_account(self):
        self.run_day(FakeGmail())
        other = FakeGmail()
        with self.assertRaises(ValueError):
            daily.run(self.db, other, NOW, "different@example.com", date(2026, 10, 7),
                      lambda *_: None, lambda *_: None)
        self.assertEqual(other.calls, 0)

    def test_running_lease_cannot_be_marked_abandoned(self):
        gmail = FakeGmail()
        with daily.lock_manifest(self.db):
            self.assertEqual(self.run_day(gmail), 0)
            self.assertEqual(gmail.calls, 0)
        self.assertEqual(self.run_day(gmail), 1)

    def test_backlog_notice_failure_does_not_skip_today(self):
        gmail = FakeGmail()
        def broken_notice(day, _reason):
            if day == "2026-10-06":
                raise RuntimeError("synthetic Kanban outage")
        def admit_backlog(day, message, key):
            self.admitted.append((message["id"], key))
            return "admitted"
        with self.assertRaisesRegex(RuntimeError, "inbox notice unavailable"):
            daily.run(self.db, gmail, NOW, "zhachory1@gmail.com", date(2026, 10, 6),
                      broken_notice, admit_backlog)
        self.assertEqual([mid for mid, _ in self.admitted], ["one"])
        self.assertEqual(self.run_day(gmail, since=date(2026, 10, 6)), 0)
        self.assertEqual(gmail.calls, 1)
        self.assertIn(("2026-10-06", "missed-run"), self.notices)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status FROM days WHERE day='2026-10-07'").fetchone(), ("complete",))

    def test_partial_admission_only_manual_replay_processes_unseen(self):
        gmail = FakeGmail(("one", "two"))
        keys = {}
        def partial(day, message, key):
            keys[message["id"]] = key
            if message["id"] == "two":
                raise RuntimeError("synthetic crash after first decision")
            return "skipped"
        with self.assertRaises(RuntimeError):
            self.run_day(gmail, admit=partial)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT message_id,outcome FROM seen").fetchall(), [("one", "skipped")])
            self.assertEqual(conn.execute("SELECT status FROM days").fetchone(), ("failed",))
        self.assertEqual(self.run_day(gmail), 0)
        self.assertEqual(self.admitted, [])
        replayed = []
        def retry(day, message, key):
            replayed.append((day, message["id"], key))
            return "admitted"
        count = daily.replay_failed(self.db, gmail, NOW, "zhachory1@gmail.com", date(2026, 10, 7),
                                    lambda day, reason: self.notices.append((day, reason)), retry)
        self.assertEqual(count, 1)
        self.assertEqual(replayed, [("2026-10-07", "two", keys["two"])])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status FROM days").fetchone(), ("complete",))
            self.assertEqual(conn.execute("SELECT message_id,outcome FROM seen ORDER BY message_id").fetchall(),
                             [("one", "skipped"), ("two", "admitted")])

    def test_replay_reuses_card_key_after_ambiguous_admission(self):
        gmail = FakeGmail()
        created = set()
        def interrupted(day, message, key):
            created.add(key)
            raise RuntimeError("synthetic crash after Kanban create")
        with self.assertRaises(RuntimeError):
            self.run_day(gmail, admit=interrupted)
        def reconcile(day, message, key):
            self.assertIn(key, created)
            return "admitted"
        daily.replay_failed(self.db, gmail, NOW, "zhachory1@gmail.com", date(2026, 10, 7),
                            lambda day, reason: self.notices.append((day, reason)), reconcile)
        self.assertEqual(len(created), 1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT message_id,outcome FROM seen").fetchall(), [("one", "admitted")])

    def test_complete_day_cannot_be_manually_replayed(self):
        self.run_day(FakeGmail())
        with self.assertRaises(ValueError):
            daily.replay_failed(self.db, FakeGmail(), NOW, "zhachory1@gmail.com", date(2026, 10, 7),
                                lambda *_: None, lambda *_: "admitted")

    def test_admission_failure_cannot_mark_complete(self):
        def fail(_day, _message, _key):
            raise RuntimeError("synthetic admission failure")
        with self.assertRaises(RuntimeError):
            self.run_day(FakeGmail(), admit=fail)
        self.assertEqual(self.notices, [("2026-10-07", "admission-error")])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status,count FROM days").fetchone(), ("failed", None))

    def test_overflow_fails_visibly_and_never_replays(self):
        gmail = FakeGmail(tuple(str(i) for i in range(251)))
        with self.assertRaises(daily.InboxOverflow):
            self.run_day(gmail)
        self.assertEqual(self.notices, [("2026-10-07", "overflow")])
        self.assertEqual(self.run_day(gmail), 0)
        self.assertEqual(gmail.calls, 1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status, reason FROM days").fetchone(),
                             ("failed", "overflow"))

    def test_explicit_overflow_replay_uses_current_inbox_set_for_original_day(self):
        gmail = FakeGmail(tuple(str(i) for i in range(251)))
        with self.assertRaises(daily.InboxOverflow):
            self.run_day(gmail)
        with self.assertRaises(daily.InboxOverflow):
            daily.replay_failed(self.db, gmail, NOW, "zhachory1@gmail.com", date(2026, 10, 7),
                                lambda *_: None, lambda *_: "admitted")
        gmail.ids = tuple(str(i) for i in range(1, 251))
        count = daily.replay_failed(self.db, gmail, NOW, "zhachory1@gmail.com", date(2026, 10, 7),
                                    lambda *_: None, lambda day, message, key: "admitted")
        self.assertEqual(count, 250)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status,count FROM days").fetchone(), ("complete", 250))
            self.assertEqual(conn.execute("SELECT count(*) FROM seen").fetchone()[0], 250)
            self.assertIsNone(conn.execute("SELECT 1 FROM seen WHERE message_id='0'").fetchone())

    def test_notice_uses_existing_board_with_stable_key_and_no_mail_content(self):
        with mock.patch.object(daily.subprocess, "run") as command:
            command.return_value.returncode = 0
            daily.kanban_notice("/fake/hermes", "2026-10-07", "overflow")
        args = command.call_args.args[0]
        self.assertEqual(args[:4], ["/fake/hermes", "kanban", "--board", "inbox-replies"])
        self.assertIn("--initial-status", args)
        self.assertEqual(args[args.index("--initial-status") + 1], "blocked")
        self.assertEqual(args[args.index("--idempotency-key") + 1], "inbox-alert-2026-10-07-overflow")
        self.assertNotIn("Approve", " ".join(args))

    def test_old_started_run_fails_without_backfill(self):
        daily.open_manifest(self.db).close()
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO days(day,status,started_at,lease) VALUES ('2026-10-07','started',1,'old')")
        gmail = FakeGmail()
        self.assertEqual(self.run_day(gmail), 0)
        self.assertEqual(gmail.calls, 0)
        self.assertEqual(self.notices, [("2026-10-07", "abandoned")])

    def test_missed_day_is_reported_on_next_run_not_backfilled(self):
        gmail = FakeGmail()
        self.run_day(gmail, since=date(2026, 10, 6))
        self.assertEqual(self.notices, [("2026-10-06", "missed-run")])
        self.assertEqual(gmail.calls, 1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status,reason FROM days WHERE day='2026-10-06'").fetchone(),
                             ("failed", "missed-run"))


if __name__ == "__main__":
    unittest.main()
