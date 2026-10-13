#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('council_tool_parent',str(ROOT/'bin/hermes-council-tools'))
tool=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(tool)

class ParentTest(unittest.TestCase):
    def test_synthesis_sees_only_its_complete_pinned_parent_handoffs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            for name in ('workspace','snapshot','input'):(root/name).mkdir()
            (root/'workspace/.council-tools.json').write_text(json.dumps({
                'schema_version':1,'snapshot_root':str(root/'snapshot'),'input_root':str(root/'input')}))
            (root/'workspace/.council-tools.json').chmod(0o440)
            db=root/'board.db';conn=sqlite3.connect(db);self.addCleanup(conn.close);conn.row_factory=sqlite3.Row
            conn.execute('CREATE TABLE task_runs (id INTEGER,task_id TEXT,claim_lock TEXT,status TEXT)')
            conn.execute("INSERT INTO task_runs VALUES (41,'own','lock','running')")
            roles=('generalist','reliability','mvp')
            own=SimpleNamespace(assignee='pr-review-synthesis-v2',workspace_path=str(root/'workspace'),
                body=json.dumps({'role':'synthesis','operation_id':'op','artifact_digest':'d'*64,'parents':list(roles)}))
            parents={role:SimpleNamespace(status='done',assignee=f'pr-review-{role}-v2',
                body=json.dumps({'role':role,'operation_id':'op','artifact_digest':'d'*64})) for role in roles}
            kb=ModuleType('hermes_cli.kanban_db')
            kb.kanban_db_path=lambda board:db
            kb.get_task=lambda conn,id:own if id=='own' else parents[id]
            kb.parent_ids=lambda conn,id:list(roles)
            kb.list_runs=lambda conn,id:[SimpleNamespace(outcome='completed',profile=parents[id].assignee,
                metadata={'role':id,'operation_id':'op','artifact_digest':'d'*64,'verdict':'clear','findings':[]})]
            kbc=ModuleType('hermes_cli.kanban_db_connect')
            class Context:
                def __enter__(self):return conn
                def __exit__(self,*_):pass
            kbc.connect_closing=lambda board:Context()
            package=ModuleType('hermes_cli');package.kanban_db=kb;package.kanban_db_connect=kbc
            aliases={'profile':'pr-review-synthesis-v2','board':'pr-review','workspace':str(root/'workspace'),
                     'snapshot_root':str(root/'snapshot'),'workflow_root':str(root/'input'),
                     'db':str(db),'task':'own','run_id':'41','claim_lock':'lock'}
            with mock.patch.dict(sys.modules,{'hermes_cli':package,'hermes_cli.kanban_db':kb,
                                               'hermes_cli.kanban_db_connect':kbc}):
                self.assertEqual(set(json.loads(tool.parent_handoffs(aliases))),set(roles))
                aliases['profile']='pr-review-generalist-v2'
                with self.assertRaises(ValueError):tool.parent_handoffs(aliases)
                aliases['profile']='pr-review-synthesis-v2';parents['mvp'].status='running'
                with self.assertRaises(ValueError):tool.parent_handoffs(aliases)

if __name__=='__main__':unittest.main()
