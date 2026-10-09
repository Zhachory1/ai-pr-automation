#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
loader=importlib.machinery.SourceFileLoader('council_publisher_redact',str(ROOT/'scripts/hermes-pr-review-council-publish.py'))
publisher=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(publisher)

class RedactionTest(unittest.TestCase):
    def test_verified_council_evidence_redacted_without_deleting_task_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();operation='pr-review-'+'a'*64;artifact='d'*64
            workspace=root/operation;workspace.mkdir(mode=0o700)
            effect=workspace/'review-effects.sqlite'
            with closing(publisher._db(effect)) as connection:
                with connection:
                    connection.execute('INSERT INTO effects VALUES (?,?,?,?,?,?,?,?,?,?)',
                                       (operation,'example/repo',17,'a'*40,'b'*40,artifact,'c'*64,'APPROVE','verified',7))
            receipt={'status':'verified','operation_id':operation,'artifact_digest':artifact,
                     'head_sha':'a'*40,'base_sha':'b'*40,'diff_digest':'1'*64,
                     'context_digest':'2'*64,'task_id':'t_00000004','review_id':7}
            publisher.COUNCIL._immutable_file(workspace/'review-outcome.json',
                                               json.dumps(receipt,sort_keys=True,separators=(',',':')).encode())
            db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.row_factory=sqlite3.Row
            db.executescript('''CREATE TABLE tasks(id TEXT,idempotency_key TEXT,assignee TEXT,status TEXT,body TEXT,result TEXT,last_failure_error TEXT);
                CREATE TABLE task_runs(id INTEGER,task_id TEXT,profile TEXT,outcome TEXT,ended_at INTEGER,summary TEXT,metadata TEXT,error TEXT);
                CREATE TABLE task_comments(task_id TEXT,body TEXT);
                CREATE TABLE task_events(task_id TEXT,payload TEXT);
                CREATE TABLE task_attachments(task_id TEXT);''')
            profiles={'generalist':'pr-review-generalist-v2','reliability':'pr-review-reliability-v2',
                      'mvp':'pr-review-mvp-v2','synthesis':'pr-review-synthesis-v2'}
            for number,(role,profile) in enumerate(profiles.items(),1):
                task_id=f't_{number:08x}'
                body=json.dumps({'operation_id':operation,'artifact_digest':artifact,'role':role})
                db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)',
                           (task_id,'pr-review-council-'+artifact[:32]+':'+role,profile,'done',body,'private result',None))
                db.execute('INSERT INTO task_runs VALUES (?,?,?,?,?,?,?,?)',
                           (number,task_id,profile,'completed',123,'private summary','{"evidence":"private"}',None))
                db.execute('INSERT INTO task_comments VALUES (?,?)',(task_id,'private comment'))
                db.execute('INSERT INTO task_events VALUES (?,?)',(task_id,'{"summary":"private"}'))
            db.commit()
            publisher.redact_verified(db,effect,workspace,receipt)
            publisher.redact_verified(db,effect,workspace,receipt)
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT count(*) FROM task_runs').fetchone()[0],4)
            for table,column in (('tasks','result'),('task_runs','summary'),('task_runs','metadata'),
                                 ('task_comments','body'),('task_events','payload')):
                values=[row[0] for row in db.execute(f'SELECT {column} FROM {table}')]
                self.assertNotIn('private',str(values))
            self.assertTrue((workspace/'review-cleanup.json').exists())

if __name__=='__main__':unittest.main()
