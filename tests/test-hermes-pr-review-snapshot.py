#!/usr/bin/env python3
import importlib.util
import json
import os
import sqlite3
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('review_snapshot', ROOT / 'scripts/hermes-pr-review-snapshot.py')
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


class SnapshotTest(unittest.TestCase):
    def test_sol_parent_creates_luna_children_and_one_sol_final_card(self):
        soul=(ROOT/'agent-config/hermes/profiles/pr-review-v1/SOUL.md').read_text()
        self.assertIn('Parent card (no `role` field): orchestrate; do not post',soul)
        for role in ('pr-review-reliability-v2','pr-review-mvp-v2','pr-review-security-v2'):
            self.assertIn(role,soul)
        self.assertIn('parents=[your task ID]',soul)
        self.assertIn('parents=[your task ID, all selected reviewer task IDs]',soul)
        self.assertIn('model=gpt-6-luna',soul)
        self.assertIn('role="synthesis"',soul)
        self.assertIn('only this card may publish',soul)

    def test_pinned_luna_workspaces_replay_and_verified_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            repo = root / 'repo'; repo.mkdir()
            def git(*args):
                return subprocess.run(['git', '-C', str(repo), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            git('init'); git('config', 'user.email', 'fixture@example.test'); git('config', 'user.name', 'fixture')
            (repo/'file.py').write_text('old\n'); git('add', 'file.py'); git('commit', '-m', 'base')
            base=git('rev-parse','HEAD')
            (repo/'file.py').write_text('new\n'); git('commit','-am','head'); head=git('rev-parse','HEAD')
            operation=snapshot.COUNCIL.identity('pr-review','example/repo',17,head)['operation_id']
            workspace=root/operation; workspace.mkdir(mode=0o700)
            request={'operation_id':operation,'repo':'example/repo','number':17,
                     'url':'https://github.com/example/repo/pull/17','title':'synthetic','head_sha':head}
            snapshot.ENQUEUE.immutable(workspace/'request.json',snapshot.ENQUEUE.canonical(request))
            class GitHub:
                def state(self,*_):return {'state':'open','head_sha':head,'base_sha':base}
                def pr(self,*_):return {'state':'open','head_sha':head,'base_sha':base,
                                        'changed_files':1,'body':'synthetic intent'}
                def files(self,*_):return [{'filename':'file.py','patch':'@@ -1 +1 @@\n+new'}]
                def reviews(self,*_):return []
                def comments(self,*_):return []
                def checks(self,*_):return []
                def actor(self):return 'review-bot'
                def reviews_for_head(self,*_):return [{'id':7,'commit_id':head,'author':'review-bot',
                    'state':'APPROVE','body':f'<!-- ai-pr-automation head={head} -->'}]
                def post(self,*_):raise AssertionError('snapshot helper must not post')
            github=GitHub()
            with mock.patch.object(snapshot.LOCAL,'resolve',return_value=repo):
                prepared=snapshot.prepare(workspace,github)
                self.assertEqual(prepared,snapshot.prepare(workspace,github))
                with mock.patch.object(github,'state',return_value={'state':'open','head_sha':'e'*40,'base_sha':base}):
                    with self.assertRaisesRegex(ValueError,'head moved'):
                        snapshot.prepare(workspace,github)
            self.assertEqual(prepared['roles'],['reliability','mvp'])
            self.assertEqual(set(prepared['workspaces']),{'reliability','mvp','synthesis'})
            for path in prepared['workspaces'].values():
                bound=json.loads((pathlib.Path(path)/'.council-tools.json').read_text())
                self.assertEqual((bound['snapshot_root'],bound['head_sha']),(str(repo),head))
            private=(workspace/'input/context.json').read_bytes()
            board=root/'board.db'
            with sqlite3.connect(board) as db:
                db.executescript('CREATE TABLE tasks(id TEXT,idempotency_key TEXT,assignee TEXT,status TEXT,body TEXT,result TEXT,last_failure_error TEXT,model_override TEXT,provider_override TEXT);'
                                 'CREATE TABLE task_runs(task_id TEXT,profile TEXT,outcome TEXT,ended_at INTEGER,summary TEXT,metadata TEXT,error TEXT);'
                                 'CREATE TABLE task_attachments(task_id TEXT);'
                                 'CREATE TABLE task_comments(task_id TEXT,author TEXT,body TEXT);'
                                 'CREATE TABLE task_events(task_id TEXT,payload TEXT);')
                for index,role in enumerate(prepared['roles'],1):
                    task_id=f't_{index:08x}'
                    db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                        (task_id,f'{prepared["workflow_id"]}:{role}',f'pr-review-{role}-v2','done',
                         json.dumps({'role':role,'operation_id':operation,'artifact_digest':prepared['artifact_digest']}),
                         'private result',None,'gpt-6-luna','openai-codex'))
                    db.execute('INSERT INTO task_runs VALUES (?,?,?,?,?,?,?)',
                        (task_id,f'pr-review-{role}-v2','completed',1,'private summary','{"evidence":"private"}',None))
                    db.execute('INSERT INTO task_comments VALUES (?,?,?)',
                        (task_id,f'pr-review-{role}-v2','private comment'))
                    db.execute('INSERT INTO task_events VALUES (?,?)',(task_id,'private event'))
                db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                    ('t_00000003',f'{prepared["workflow_id"]}:final','pr-review-v1','running',
                     json.dumps({'role':'synthesis','operation_id':operation,
                                 'artifact_digest':prepared['artifact_digest']}),None,None,
                     'gpt-6-sol','openai-codex'))
            with mock.patch.dict(os.environ,{'HERMES_KANBAN_DB':str(board)}):
                with self.assertRaisesRegex(ValueError,'not verified'):
                    snapshot.cleanup(workspace,8,'APPROVE',github)
                with self.assertRaisesRegex(ValueError,'not verified'):
                    snapshot.cleanup(workspace,7,'COMMENT',github)
                with sqlite3.connect(board) as db:
                    db.execute("UPDATE tasks SET model_override='wrong' WHERE id='t_00000001'")
                with self.assertRaisesRegex(ValueError,'lineage'):
                    snapshot.cleanup(workspace,7,'APPROVE',github)
                with sqlite3.connect(board) as db:
                    db.execute("UPDATE tasks SET model_override='gpt-6-luna' WHERE id='t_00000001'")
                    db.execute("INSERT INTO task_comments VALUES ('t_00000001','operator','keep this note')")
                with self.assertRaisesRegex(ValueError,'operator comment'):
                    snapshot.cleanup(workspace,7,'APPROVE',github)
                with sqlite3.connect(board) as db:
                    self.assertEqual(db.execute("SELECT body FROM task_comments WHERE author='operator'").fetchone(),
                                     ('keep this note',))
                    db.execute("DELETE FROM task_comments WHERE author='operator'")
                    db.execute("INSERT INTO task_comments VALUES ('t_00000001',NULL,'anonymous human note')")
                with self.assertRaisesRegex(ValueError,'operator comment'):
                    snapshot.cleanup(workspace,7,'APPROVE',github)
                with sqlite3.connect(board) as db:
                    db.execute('DELETE FROM task_comments WHERE author IS NULL')
                snapshot.cleanup(workspace,7,'APPROVE',github)
                self.assertFalse((workspace/'input/context.json').exists())
                self.assertFalse((workspace/'input/identity.json').exists())
                with sqlite3.connect(board) as db:
                    self.assertEqual(db.execute('SELECT metadata FROM task_runs').fetchall(),[(None,),(None,)])
                    self.assertEqual(db.execute('SELECT body FROM task_comments').fetchall(),[('[redacted]',),('[redacted]',)])
                (workspace/'input/context.json').write_bytes(private)
                (workspace/'input/context.json').chmod(0o400)
                snapshot.cleanup(workspace,7,'APPROVE',github)
                self.assertFalse((workspace/'input/context.json').exists())


if __name__=='__main__':unittest.main()
