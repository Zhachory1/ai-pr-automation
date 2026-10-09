#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import json
import hashlib
import subprocess
import sqlite3
import sys
from contextlib import closing, contextmanager
from types import ModuleType
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('review_worker', str(ROOT/'scripts/hermes-pr-review-council-worker.py'))
spec = importlib.util.spec_from_loader(loader.name, loader)
worker = importlib.util.module_from_spec(spec)
loader.exec_module(worker)


class CouncilWorkerTest(unittest.TestCase):
    def test_stale_gc_has_separate_daily_schedule(self):
        root=ROOT/'launchd'
        gc=plistlib.loads((root/'com.example.ai-pr-automation-pr-review-council-gc.plist.template').read_bytes())
        normal=plistlib.loads((root/'com.example.ai-pr-automation-pr-review-council.plist.template').read_bytes())
        self.assertIn('--stale-gc',gc['ProgramArguments'])
        self.assertEqual(gc['StartCalendarInterval'],{'Hour':3,'Minute':0})
        self.assertNotIn('--stale-gc',normal['ProgramArguments'])
        self.assertEqual(normal['StartInterval'],60)

    def test_daily_gc_closes_stale_head_with_pinned_context_before_task_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();repo=root/'code/repo';repo.mkdir(parents=True)
            def git(*args):
                return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()
            git('init');git('config','user.email','fixture@example.test');git('config','user.name','fixture')
            (repo/'file.py').write_text('old\n');git('add','file.py');git('commit','-m','base');base=git('rev-parse','HEAD')
            (repo/'file.py').write_text('new\n');git('commit','-am','head');head=git('rev-parse','HEAD')
            context=b'{"checks":[],"intent":"synthetic","prior_feedback":[]}'
            diff=worker.INGRESS.COUNCIL.local_diff(repo,base,head)
            operation=worker.INGRESS.COUNCIL.identity('pr-review','example/repo',17,head)['operation_id']
            plan=worker.INGRESS.COUNCIL.plan({'operation_id':operation,'repo':'example/repo','number':17,
                   'head_sha':head,'base_sha':base,'diff_digest':hashlib.sha256(diff).hexdigest(),
                   'context_digest':hashlib.sha256(context).hexdigest(),
                   'changed_paths':['file.py'],'repository_path':str(repo)})
            workspace=root/operation;workspace.mkdir(mode=0o700)
            owner={'route':'council-v2','operation_id':operation,'repo':'example/repo',
                   'number':17,'head_sha':head,'title':'synthetic'}
            worker.INGRESS.ENQUEUE.immutable(workspace/'request.json',worker.INGRESS.ENQUEUE.canonical(owner))
            worker.INGRESS.COUNCIL.prepare_snapshot(workspace,plan,diff,context)
            (root/'authority').write_text('repos:\n  - example/repo\n')
            config=worker.INGRESS.Config(root,root/'authority',root,root/'hermes',{},
                                          council_install=root/'runtime',council_enabled=True)
            class GitHub:
                def state(self,*_):return {'state':'open','head_sha':'e'*40,'base_sha':base}
                def reviews_for_head(self,*_):return []
                def comments(self,*_):return []
            with mock.patch.object(worker.INGRESS,'validate_council_runtime'),\
                 mock.patch.object(worker.INGRESS.GITHUB,'GitHub',return_value=GitHub()),\
                 mock.patch.object(worker.INGRESS,'invoke_council',side_effect=AssertionError('stale post')):
                self.assertEqual(worker.tick(config,stale_gc=True)['status'],'superseded')
            self.assertFalse((workspace/'input/context.json').exists())
            self.assertFalse((workspace/'input/identity.json').exists())
            worker.INGRESS.council_capacity(config,'pr-review-'+'b'*64)

    def test_gc_preserves_active_and_operator_cards_and_redacts_terminal_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();home=root/'hermes';workspace=root/'operation';workspace.mkdir()
            board=home/'kanban/boards/pr-review/kanban.db';board.parent.mkdir(parents=True)
            install=root/'runtime/hermes_cli';install.mkdir(parents=True)
            (install/'kanban_db.py').write_text('fixture')
            spec=worker.INGRESS.COUNCIL.plan({'operation_id':worker.INGRESS.COUNCIL.identity(
                'pr-review','example/repo',17,'a'*40)['operation_id'],'repo':'example/repo',
                'number':17,'head_sha':'a'*40,'base_sha':'b'*40,'diff_digest':'c'*64,
                'context_digest':'d'*64,'changed_paths':['file.py']})
            with closing(sqlite3.connect(board)) as db:
                db.executescript('CREATE TABLE tasks(id TEXT,idempotency_key TEXT,assignee TEXT,body TEXT,status TEXT,workspace_path TEXT,result TEXT,last_failure_error TEXT);'
                                 'CREATE TABLE task_attachments(task_id TEXT);'
                                 'CREATE TABLE task_comments(task_id TEXT,author TEXT,body TEXT);'
                                 'CREATE TABLE task_runs(task_id TEXT,profile TEXT,status TEXT,outcome TEXT,ended_at INTEGER,worker_pid INTEGER,summary TEXT,metadata TEXT,error TEXT);'
                                 'CREATE TABLE task_events(task_id TEXT,payload TEXT);')
                for index,(role,profile) in enumerate(spec['specialists'].items(),1):
                    db.execute('INSERT INTO tasks(id,idempotency_key,assignee,body,status,workspace_path) VALUES (?,?,?,?,?,?)',
                        (f't_{index:08x}',spec['workflow_id']+':'+role,profile,
                         json.dumps({'role':role,'operation_id':spec['operation_id'],
                                     'artifact_digest':spec['artifact_digest']}),
                         'ready',str(workspace/'workspaces'/role)))
                db.execute('INSERT INTO tasks(id,idempotency_key,assignee,body,status,workspace_path) VALUES (?,?,?,?,?,?)',
                    ('t_00000004',spec['workflow_id']+':synthesis',spec['synthesis']['profile'],
                     json.dumps({'role':'synthesis','operation_id':spec['operation_id'],
                                 'artifact_digest':spec['artifact_digest']}),
                     'todo',str(workspace/'workspaces/synthesis')))
                db.execute('INSERT INTO tasks(id,idempotency_key,assignee,body,status,workspace_path) VALUES (?,?,?,?,?,?)',
                    ('t_99999999','unrelated','pr-review-v1','{}','done',str(workspace)))
                db.execute("INSERT INTO task_comments VALUES ('t_00000001','operator','human note')")
                db.execute("INSERT INTO task_comments VALUES ('t_00000001','pr-review-generalist-v2','private note')")
                db.commit()
            kbc=ModuleType('hermes_cli.kanban_db_connect')
            @contextmanager
            def connect_closing(board):
                with closing(sqlite3.connect(home/'kanban/boards/pr-review/kanban.db')) as db:
                    db.row_factory=sqlite3.Row
                    with db:yield db
            kbc.connect_closing=connect_closing
            package=ModuleType('hermes_cli');package.kanban_db_connect=kbc
            config=worker.INGRESS.Config(root,root/'authority',home,root/'hermes',{},
                                          council_install=install.parent,council_enabled=True)
            with mock.patch.dict(sys.modules,{'hermes_cli':package,'hermes_cli.kanban_db_connect':kbc}):
                with self.assertRaisesRegex(ValueError,'active'):
                    worker.gc_cards(config,spec,workspace)
                with closing(sqlite3.connect(board)) as db:
                    db.execute("UPDATE tasks SET status='done' WHERE id!='t_99999999'")
                    db.execute("DELETE FROM task_comments WHERE author='operator'")
                    db.commit()
                with self.assertRaisesRegex(ValueError,'run history'):
                    worker.gc_cards(config,spec,workspace)
                with closing(sqlite3.connect(board)) as db:
                    for index,(role,profile) in enumerate((*spec['specialists'].items(),
                                                            ('synthesis',spec['synthesis']['profile'])),1):
                        tid=f't_{index:08x}'
                        db.execute('INSERT INTO task_runs VALUES (?,?,?,?,?,?,?,?,?)',
                                   (tid,profile,'done','completed',1,None,
                                    'private summary','{"evidence":"private"}',None))
                        db.execute('INSERT INTO task_events VALUES (?,?)',(tid,'{"evidence":"private"}'))
                    db.execute("INSERT INTO task_comments VALUES ('t_00000001','operator','human note')")
                    db.commit()
                with self.assertRaisesRegex(ValueError,'operator comments'):
                    worker.gc_cards(config,spec,workspace)
                with closing(sqlite3.connect(board)) as db:
                    db.execute("DELETE FROM task_comments WHERE author='operator'");db.commit()
                worker.gc_cards(config,spec,workspace)
            with closing(sqlite3.connect(board)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],5)
                self.assertEqual(db.execute('SELECT metadata FROM task_runs').fetchall(),[(None,)]*4)
                self.assertEqual(db.execute('SELECT body FROM task_comments').fetchall(),[('[redacted]',)])
                self.assertEqual(db.execute('SELECT payload FROM task_events').fetchall(),[(None,)]*4)

    def test_daily_gc_finishes_verified_receipt_cleanup_without_posting_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();operation='pr-review-'+'a'*64;workspace=root/operation
            workspace.mkdir(mode=0o700)
            owner={'route':'council-v2','operation_id':operation,'repo':'example/repo',
                   'number':17,'head_sha':'a'*40,'title':'synthetic'}
            worker.INGRESS.ENQUEUE.immutable(workspace/'request.json',worker.INGRESS.ENQUEUE.canonical(owner))
            receipt={'status':'verified','operation_id':operation,'head_sha':'a'*40,'task_id':'t_00000001',
                     'review_id':7}
            worker.INGRESS.COUNCIL._immutable_file(workspace/'review-outcome.json',
                                                   json.dumps(receipt).encode())
            (root/'authority').write_text('repos:\n  - example/repo\n')
            config=worker.INGRESS.Config(root,root/'authority',root,root/'hermes',{},
                                          council_install=root/'runtime',council_enabled=True)
            with mock.patch.object(worker.INGRESS,'validate_council_runtime'),\
                 mock.patch.object(worker.INGRESS,'invoke_council',return_value={'status':'done'}) as invoked:
                self.assertEqual(worker.tick(config,stale_gc=True)['status'],'done')
            invoked.assert_called_once()

    def test_stale_pending_head_releases_capacity_without_posting(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); op='pr-review-'+'a'*64; workspace=root/op
            workspace.mkdir(mode=0o700)
            owner={'route':'council-v2','operation_id':op,'repo':'example/repo',
                   'number':17,'head_sha':'a'*40,'title':'synthetic'}
            worker.INGRESS.ENQUEUE.immutable(workspace/'request.json',worker.INGRESS.ENQUEUE.canonical(owner))
            (root/'authority').write_text('repos:\n  - example/repo\n')
            config=worker.INGRESS.Config(root,root/'authority',root,root/'hermes',{},
                                          council_install=root/'runtime',council_enabled=True)
            class GitHub:
                def state(self,*_):return {'state':'open','head_sha':'e'*40,'base_sha':'b'*40}
                def reviews_for_head(self,*_):return []
                def comments(self,*_):return []
            with mock.patch.object(worker.INGRESS,'validate_council_runtime'),\
                 mock.patch.object(worker.INGRESS.GITHUB,'GitHub',return_value=GitHub()),\
                 mock.patch.object(worker.INGRESS,'invoke_council',side_effect=AssertionError('stale post')):
                self.assertEqual(worker.tick(config,stale_gc=True)['status'],'superseded')
            self.assertEqual(json.loads((workspace/'review-outcome.json').read_text())['status'],'superseded')
            worker.INGRESS.council_capacity(config,'pr-review-'+'b'*64)

    def test_normal_pass_services_both_active_heads(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();(root/'authority').write_text('repos:\n  - example/repo\n')
            for number in (17,18):
                operation=worker.INGRESS.COUNCIL.identity('pr-review','example/repo',number,'a'*40)['operation_id']
                workspace=root/operation;workspace.mkdir(mode=0o700)
                owner={'route':'council-v2','operation_id':operation,'repo':'example/repo',
                       'number':number,'head_sha':'a'*40,'title':'synthetic'}
                worker.INGRESS.ENQUEUE.immutable(workspace/'request.json',worker.INGRESS.ENQUEUE.canonical(owner))
            config=worker.INGRESS.Config(root,root/'authority',root,root/'hermes',{},
                                          council_install=root/'runtime',council_enabled=True)
            with mock.patch.object(worker.INGRESS,'validate_council_runtime'),\
                 mock.patch.object(worker.LOCAL,'resolve',return_value=root/'checkout'),\
                 mock.patch.object(worker.INGRESS,'invoke_council',return_value={'status':'active'}) as invoked:
                self.assertEqual(worker.tick(config)['status'],'active')
            self.assertEqual(invoked.call_count,2)

    def test_revisits_pending_owner_without_another_discovery_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            operation = 'pr-review-' + 'a'*64
            workspace = root/operation
            workspace.mkdir(mode=0o700)
            owner = {'route':'council-v2','operation_id':operation,'repo':'example/repo',
                     'number':17,'head_sha':'a'*40,'title':'synthetic'}
            (root/'authority').write_text('repos:\n  - example/repo\n')
            worker.INGRESS.ENQUEUE.immutable(workspace/'request.json',worker.INGRESS.ENQUEUE.canonical(owner))
            config = worker.INGRESS.Config(root,root/'authority',root,root/'hermes',{},
                                            council_install=root/'runtime',council_enabled=True)
            result = {'kind':'pr-review','board':'pr-review','operation_id':operation,
                      'task_id':'t_00000001','status':'active'}
            class GitHub:
                def state(self,*_):return {'state':'open','head_sha':'a'*40,'base_sha':'b'*40}
            with mock.patch.object(worker.INGRESS,'validate_council_runtime'),\
                 mock.patch.object(worker.INGRESS.GITHUB,'GitHub',return_value=GitHub()),\
                 mock.patch.object(worker.LOCAL,'resolve',return_value=root/'checkout') as located,\
                 mock.patch.object(worker.INGRESS,'invoke_council',return_value=result) as invoked:
                self.assertEqual(worker.tick(config)['status'],'active')
            invoked.assert_called_once()
            request = invoked.call_args.args[1]
            located.assert_called_once_with('example/repo','a'*40)
            self.assertEqual(invoked.call_args.args[2],root/'checkout')
            self.assertEqual(request['operation_id'],operation)
            self.assertEqual(request['url'],'https://github.com/example/repo/pull/17')
            self.assertFalse((workspace/'review-outcome.json').exists())


if __name__ == '__main__':
    unittest.main()
