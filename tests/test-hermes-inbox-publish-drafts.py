#!/usr/bin/env python3
from contextlib import closing
from email.parser import BytesParser
from email.policy import SMTP
import hashlib
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
import hermes_inbox_gmail_draft_publish as publish
import hermes_inbox_stage_card as stage_card


ACCOUNT='zhachory1@gmail.com'
KEY='inbox-message-'+hashlib.sha256(b'zhachory1@gmail.com\0source1').hexdigest()
HEADERS=[{'name':'From','value':'Sender <sender@example.com>'},
         {'name':'Subject','value':'Question'},
         {'name':'Message-ID','value':'<source@example.com>'}]


class Reader:
    def __init__(self):self.head='source1'
    def profile(self):return {'emailAddress':ACCOUNT}
    def get_thread(self,thread_id):
        assert thread_id=='thread1'
        return [{'id':self.head,'internalDate':'1760000000000','text':'Synthetic request','headers':HEADERS}]


class Drafts:
    def __init__(self):self.created=[];self.fail=False;self.fail_receipt=False
    def profile(self):return {'emailAddress':ACCOUNT}
    def create(self,mime,thread_id):
        self.created.append((mime,thread_id))
        if self.fail:raise TimeoutError('synthetic ambiguous draft create')
        return 'draft1'
    def get(self,draft_id):
        if self.fail_receipt:raise TimeoutError('synthetic receipt timeout after remote create')
        mime,thread_id=self.created[0]
        message=BytesParser(policy=SMTP).parsebytes(mime)
        names=('From','To','Subject','In-Reply-To','References','Message-ID')
        return {'id':draft_id,'message':{'id':'draft-message','threadId':thread_id,'labelIds':['DRAFT'],
                'headers':[{'name':name,'value':('<gmail-generated@example.com>' if name=='Message-ID'
                                                     else str(message[name]))} for name in names]}}


class Ops:
    def edit_task(self,conn,task_id,*,body):
        conn.execute('UPDATE tasks SET body=? WHERE id=?',(body,task_id))
        conn.execute("INSERT INTO task_events(task_id,kind) VALUES (?, 'edited')",(task_id,))
        conn.commit();return True
    def assign_task(self,conn,task_id,assignee):
        conn.execute('UPDATE tasks SET assignee=? WHERE id=?',(assignee,task_id))
        conn.execute("INSERT INTO task_events(task_id,kind) VALUES (?, 'assigned')",(task_id,))
        conn.commit();return True


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ledger=pathlib.Path(self.temp.name)/'drafts.sqlite'
        self.board=sqlite3.connect(':memory:');self.addCleanup(self.board.close)
        self.board.row_factory=sqlite3.Row
        self.board.executescript('''
          CREATE TABLE tasks(id TEXT PRIMARY KEY,body TEXT,status TEXT,assignee TEXT,claim_lock TEXT,
                             created_by TEXT,idempotency_key TEXT);
          CREATE TABLE task_comments(id INTEGER PRIMARY KEY,task_id TEXT,author TEXT,body TEXT,created_at INTEGER);
          CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id TEXT,kind TEXT,payload TEXT,created_at INTEGER);
        ''')
        source={'account':ACCOUNT,'message_id':'source1','thread_id':'thread1',
                'route':'help','source_day':'2026-10-07'}
        self.board.execute("INSERT INTO tasks VALUES (?,?, 'blocked','inbox-sol',NULL,'inbox-intake',?)",
                           ('t_12345678',json.dumps(source),KEY))
        self.board.commit()
        self.reader=Reader();self.drafts=Drafts()
        self.staged=stage_card.stage(self.board,self.ledger,'t_12345678',self.reader,
                                     'Thanks. Could you share the scope?',ops=Ops())

    def publish(self):
        return publish.create(self.board,self.ledger,'t_12345678',self.reader,self.drafts)

    def state(self):
        with closing(sqlite3.connect(self.ledger)) as conn:
            return conn.execute('SELECT status,draft_id FROM gmail_drafts').fetchone()

    def test_create_one_gmail_draft_without_approve_or_ready(self):
        self.assertEqual(self.publish(),'draft1')
        self.assertEqual(self.publish(),'draft1')
        self.assertEqual(len(self.drafts.created),1)
        self.assertEqual(self.state(),('created','draft1'))
        self.assertEqual(self.board.execute('SELECT status FROM tasks').fetchone()[0],'blocked')
        self.assertEqual(self.board.execute("SELECT count(*) FROM task_events WHERE kind='gmail_draft_created'").fetchone()[0],1)

    def test_abandoned_started_draft_becomes_manual_reconcile_without_recreate(self):
        with closing(sqlite3.connect(self.ledger)) as conn:
            with conn:
                conn.execute("INSERT INTO gmail_drafts(task_id,version,digest,status,draft_id) "
                             "VALUES ('t_12345678',1,?,'started',NULL)",(self.staged['digest'],))
        self.assertEqual(publish.recover_started(self.ledger),1)
        self.assertEqual(self.state(),('reconcile',None))
        self.assertEqual(publish.recover_started(self.ledger),0)
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(self.drafts.created,[])

    def test_card_edit_before_create_is_not_provider_uncertainty(self):
        original = publish._card
        calls = 0
        def changed(board, task_id):
            nonlocal calls
            calls += 1
            if calls == 3:
                board.execute("UPDATE tasks SET body='edited before create'")
                board.commit()
            return original(board, task_id)
        with mock.patch.object(publish, '_card', side_effect=changed):
            with self.assertRaises(ValueError):self.publish()
        self.assertEqual(self.drafts.created, [])
        self.assertIsNone(self.state())

    def test_stale_thread_never_creates_draft(self):
        self.reader.head='new-source'
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(self.drafts.created,[])
        self.assertIsNone(self.state())

    def test_created_remote_draft_with_receipt_timeout_is_reconcile_with_id(self):
        self.drafts.fail_receipt=True
        with self.assertRaises(TimeoutError):self.publish()
        self.assertEqual(self.state(),('reconcile','draft1'))
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(len(self.drafts.created),1)
        self.drafts.fail_receipt=False
        self.assertEqual(publish.reconcile_existing(self.board,self.ledger,'t_12345678',
                                                    'draft1',self.drafts),'draft1')
        self.assertEqual(self.state(),('created','draft1'))
        self.assertEqual(len(self.drafts.created),1)

    def test_ambiguous_draft_create_never_retries(self):
        self.drafts.fail=True
        with self.assertRaises(TimeoutError):self.publish()
        self.assertEqual(self.state()[0],'reconcile')
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(len(self.drafts.created),1)


if __name__=='__main__':unittest.main()
