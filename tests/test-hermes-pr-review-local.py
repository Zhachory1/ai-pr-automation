#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import json
from contextlib import closing
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('review_local', str(ROOT/'scripts/hermes-pr-review-local.py'))
module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(module)
council_loader = importlib.machinery.SourceFileLoader('council_for_local', str(ROOT/'scripts/hermes-pr-review-council.py'))
import sys
sys.path.insert(0,str(ROOT/'scripts'))
council = importlib.util.module_from_spec(importlib.util.spec_from_loader(council_loader.name,council_loader))
council_loader.exec_module(council)
tool_loader=importlib.machinery.SourceFileLoader('council_tool_local',str(ROOT/'bin/hermes-council-tools'))
tool=importlib.util.module_from_spec(importlib.util.spec_from_loader(tool_loader.name,tool_loader))
tool_loader.exec_module(tool)
pub_loader=importlib.machinery.SourceFileLoader('council_pub_local',str(ROOT/'scripts/hermes-pr-review-council-publish.py'))
publisher=importlib.util.module_from_spec(importlib.util.spec_from_loader(pub_loader.name,pub_loader))
pub_loader.exec_module(publisher)


class LocalCheckoutTest(unittest.TestCase):
    def test_does_not_select_a_sibling_feature_worktree(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'code';sibling=root/'widget-feature';sibling.mkdir(parents=True)
            subprocess.run(['git','-C',str(sibling),'init'],check=True,capture_output=True)
            subprocess.run(['git','-C',str(sibling),'remote','add','origin',
                            'https://github.com/example/widget.git'],check=True,capture_output=True)
            with self.assertRaisesRegex(ValueError,'matching local checkout not found'):
                module.resolve('example/widget','a'*40,root=root,fetch=False)

    def test_workers_share_pinned_git_checkout_without_a_diff_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();repo=root/'code/widget';repo.mkdir(parents=True)
            def git(*args):
                return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()
            git('init');git('config','user.email','review@example.test');git('config','user.name','review')
            git('remote','add','origin','https://github.com/example/widget.git')
            (repo/'file.py').write_text('old\n');git('add','file.py');git('commit','-m','base');base=git('rev-parse','HEAD')
            (repo/'file.py').write_text('new\n');git('commit','-am','review');head=git('rev-parse','HEAD')
            (repo/'file.py').write_text('dirty and not in the review\n')
            class GitHub:
                def pr(self,*_):return {'state':'open','head_sha':head,'base_sha':base,'changed_files':1,'body':'goal'}
                def files(self,*_):return [{'filename':'file.py','patch':'+new'}]
                def diff(self,*_):raise AssertionError('remote diff should not replace local Git')
                def reviews(self,*_):return []
                def comments(self,*_):return []
                def checks(self,*_):return []
            found=module.resolve('example/widget',head,root=root/'code')
            request,diff,context=council.collect(GitHub(),'example/widget',17,head,found)
            spec=council.plan(request)
            workspace=root/'operation'
            council.prepare_snapshot(workspace,spec,diff,context)
            self.assertFalse((workspace/'snapshot').exists())
            self.assertEqual(json.loads((workspace/'input/identity.json').read_text())['repository_path'],str(repo))
            council._verified_snapshot(workspace,spec)
            self.assertNotIn(b'dirty',diff)
            conn=sqlite3.connect(':memory:');self.addCleanup(conn.close);conn.row_factory=sqlite3.Row
            conn.executescript('CREATE TABLE tasks(id TEXT,idempotency_key TEXT,assignee TEXT,body TEXT);'
                               'CREATE TABLE task_links(parent_id TEXT,child_id TEXT);')
            class KB:
                def create_task(self,db,**params):
                    task_id='t_%08x'%(db.execute('SELECT count(*) FROM tasks').fetchone()[0]+1)
                    db.execute('INSERT INTO tasks VALUES (?,?,?,?)',
                               (task_id,params['idempotency_key'],params['assignee'],params['body']))
                    for parent in params.get('parents',()):db.execute('INSERT INTO task_links VALUES (?,?)',(parent,task_id))
                    db.commit();return task_id
            council.setup(KB(),conn,spec,workspace)
            self.assertEqual(conn.execute('SELECT count(*) FROM tasks').fetchone()[0],4)
            for row in conn.execute('SELECT body FROM tasks'):
                self.assertEqual(json.loads(row[0])['repository_path'],str(repo))
            for role in ('generalist','reliability','mvp','synthesis'):
                self.assertEqual(json.loads((workspace/'workspaces'/role/'.council-tools.json').read_text())['head_sha'],head)
            aliases={'workspace':str(workspace/'workspaces/generalist'),
                     'snapshot_root':str(root/'code'),'workflow_root':str(workspace/'input'),
                     'profile':'pr-review-generalist-v2','board':'pr-review'}
            self.assertEqual(tool.snapshot_read({'path':'snapshot/file.py'},aliases),'new\n')
            self.assertIn('-old',tool.snapshot_read({'path':'snapshot/diff.patch'},aliases))
            found=json.loads(tool.snapshot_search({'path':'snapshot','query':'new','max_results':2},aliases))
            self.assertEqual(found['matches'][0]['path'],'snapshot/file.py')
            self.assertEqual(json.loads(tool.snapshot_search({'path':'snapshot','query':'dirty'},aliases))['matches'],[])
            ledger=workspace/'review-effects.sqlite'
            with closing(publisher._db(ledger)) as db:
                with db:
                    db.execute('INSERT INTO effects VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (spec['operation_id'],'example/widget',17,head,base,spec['artifact_digest'],
                                'c'*64,'APPROVE','verified',7))
            publisher.purge_verified(ledger,workspace,spec,'t_00000004')
            self.assertFalse((workspace/'input/context.json').exists())
            self.assertFalse((workspace/'snapshot').exists())
            self.assertEqual(json.loads((workspace/'review-outcome.json').read_text())['repository_path'],str(repo))

    def test_uses_existing_dirty_checkout_without_copy_or_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'code';repo=root/'widget';repo.mkdir(parents=True)
            def git(*args):
                return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()
            git('init');git('config','user.email','review@example.test');git('config','user.name','review')
            git('remote','add','origin','https://github.com/example/widget.git')
            (repo/'file.py').write_text('original\n');git('add','file.py');git('commit','-m','fixture')
            head=git('rev-parse','HEAD');(repo/'file.py').write_text('dirty uncommitted edit\n')
            self.assertEqual(module.resolve('example/widget',head,root=root),repo.resolve())
            self.assertEqual((repo/'file.py').read_text(),'dirty uncommitted edit\n')
            self.assertFalse((root/'.hermes-repos').exists())
            with self.assertRaises(ValueError):module.resolve('someone/widget',head,root=root)
            with self.assertRaises(ValueError):module.resolve('example/widget','0'*40,root=root,fetch=False)


if __name__=='__main__':unittest.main()
