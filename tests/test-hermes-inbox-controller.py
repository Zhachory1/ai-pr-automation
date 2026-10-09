#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_controller as controller


class Gmail:
    def profile(self):
        return {"emailAddress": "zhachory1@gmail.com"}

    def get_thread(self, thread_id):
        assert thread_id == "thread1"
        return [{"id": "source1", "internalDate": "1760000000000", "text": "Synthetic inbound request",
                 "headers": [{"name": "From", "value": "sender@example.com"},
                             {"name": "Subject", "value": "Synthetic question"},
                             {"name": "Message-ID", "value": "<source@example.com>"}]}]


class Ops:
    def edit_task(self, conn, task_id, *, body):
        conn.execute("UPDATE tasks SET body=? WHERE id=?", (body, task_id))
        conn.execute("INSERT INTO task_events(task_id,kind) VALUES (?, 'edited')", (task_id,))
        conn.commit(); return True

    def assign_task(self, conn, task_id, assignee):
        conn.execute("UPDATE tasks SET assignee=? WHERE id=?", (assignee, task_id))
        conn.execute("INSERT INTO task_events(task_id,kind) VALUES (?, 'assigned')", (task_id,))
        conn.commit(); return True


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.board = sqlite3.connect(":memory:");self.addCleanup(self.board.close)
        self.board.row_factory = sqlite3.Row
        self.board.executescript("""
            CREATE TABLE tasks(id TEXT PRIMARY KEY,body TEXT,status TEXT,assignee TEXT,claim_lock TEXT,
                               created_by TEXT,idempotency_key TEXT);
            CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id TEXT,kind TEXT,payload TEXT,created_at INTEGER);
            CREATE TABLE task_comments(id INTEGER PRIMARY KEY,task_id TEXT,author TEXT,body TEXT,created_at INTEGER);
        """)
        payload={"account":"zhachory1@gmail.com","message_id":"source1","thread_id":"thread1",
                 "source_day":"2026-10-07","route":"help"}
        key='inbox-message-'+hashlib.sha256(b'zhachory1@gmail.com\0source1').hexdigest()
        self.board.execute("INSERT INTO tasks VALUES (?,?, 'blocked','inbox-sol',NULL,'inbox-intake',?)",
                           ('t_12345678',json.dumps(payload),key))
        self.board.commit()
    def test_tool_free_sol_then_local_stage(self):
        with (mock.patch.object(controller.sol,"read_voice",return_value="Synthetic private voice"),
              mock.patch.object(controller.sol,"draft",return_value="Thanks. What help do you need?") as model):
            intent=controller.process(self.board,pathlib.Path(self.temp.name)/'drafts.sqlite',
                                      't_12345678',Gmail(),'/fake/hermes',pathlib.Path(self.temp.name),ops=Ops())
        self.assertEqual(intent['recipient'],'sender@example.com')
        self.assertEqual(tuple(self.board.execute("SELECT status,assignee FROM tasks").fetchone()),('blocked',None))
        self.assertEqual(json.loads(self.board.execute("SELECT body FROM tasks").fetchone()[0])['draft'],
                         'Thanks. What help do you need?')
        self.assertEqual(model.call_args.args[2],'help')

    def test_controller_resumes_preview_after_unassignment_crash_without_model(self):
        class FailedAssign(Ops):
            def assign_task(self, conn, task_id, assignee):
                raise RuntimeError('synthetic crash after preview edit')
        ledger=pathlib.Path(self.temp.name)/'drafts.sqlite'
        with (mock.patch.object(controller.sol,'read_voice',return_value='Synthetic voice'),
              mock.patch.object(controller.sol,'draft',return_value='Draft')):
            with self.assertRaises(RuntimeError):
                controller.process(self.board,ledger,'t_12345678',Gmail(),'/fake/hermes',
                                   pathlib.Path(self.temp.name),ops=FailedAssign())
        self.assertEqual(self.board.execute('SELECT assignee FROM tasks').fetchone()[0],'inbox-sol')
        with (mock.patch.object(controller.sol,'read_voice') as voice,
              mock.patch.object(controller.sol,'draft') as model):
            result=controller.process(self.board,ledger,'t_12345678',Gmail(),'/fake/hermes',
                                      pathlib.Path(self.temp.name),ops=Ops())
        self.assertEqual(result['version'],1)
        self.assertIsNone(self.board.execute('SELECT assignee FROM tasks').fetchone()[0])
        voice.assert_not_called();model.assert_not_called()

    def test_wrong_authenticated_account_rejected_before_thread_read(self):
        class WrongGmail(Gmail):
            def profile(self):
                return {"emailAddress": "other@example.com"}
            def get_thread(self, thread_id):
                raise AssertionError("must not read another account's thread")
        with self.assertRaises(ValueError):
            controller.process(self.board,pathlib.Path(self.temp.name)/'drafts.sqlite',
                               't_12345678',WrongGmail(),'/fake/hermes',pathlib.Path(self.temp.name),ops=Ops())

    def test_wrong_key_rejected_before_voice_or_model(self):
        self.board.execute("UPDATE tasks SET idempotency_key='wrong'")
        self.board.commit()
        with (mock.patch.object(controller.sol,"read_voice") as voice,
              mock.patch.object(controller.sol,"draft") as model):
            with self.assertRaises(ValueError):
                controller.process(self.board,pathlib.Path(self.temp.name)/'drafts.sqlite',
                                   't_12345678',Gmail(),'/fake/hermes',pathlib.Path(self.temp.name),ops=Ops())
        voice.assert_not_called();model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
