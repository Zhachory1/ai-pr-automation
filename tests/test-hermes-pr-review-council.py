#!/usr/bin/env python3
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import sqlite3
import tempfile
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIFF = b"diff --git a/src/a.py b/src/a.py\n+synthetic change\n"
CONTEXT = b'{"intent":"synthetic goal","checks":[],"prior_feedback":[]}'
sys.path.insert(0, str(ROOT / "scripts"))
path = ROOT / "scripts/hermes-pr-review-council.py"
loader = importlib.machinery.SourceFileLoader("premerge_council", str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
council = importlib.util.module_from_spec(spec)
loader.exec_module(council)
from hermes_direct_pr_journal import identity


class CouncilPlanTest(unittest.TestCase):
    def fixture(self, paths=("src/worker.py", "tests/test_worker.py")):
        repo, number, head = "example/repo", 17, "a" * 40
        return {"operation_id": identity("pr-review", repo, number, head)["operation_id"],
                "repo": repo, "number": number, "head_sha": head, "base_sha": "b" * 40,
                "diff_digest": hashlib.sha256(DIFF).hexdigest(),
                "context_digest": hashlib.sha256(CONTEXT).hexdigest(), "changed_paths": list(paths)}

    def test_all_new_heads_get_three_specialists_and_one_synthesis(self):
        result = council.plan(self.fixture())
        self.assertEqual(list(result["specialists"]), ["generalist", "reliability", "mvp"])
        self.assertEqual(result["specialists"]["generalist"], "pr-review-generalist-v2")
        self.assertEqual(result["synthesis"]["profile"], "pr-review-synthesis-v2")
        self.assertEqual(result["synthesis"]["parents"], list(result["specialists"]))
        self.assertNotIn("approval_brief", str(result))
        self.assertEqual(result, council.plan(self.fixture()))

    def test_security_is_selected_deterministically_by_changed_path(self):
        for name in ("src/auth/token.py", "infra/iam/policy.yaml", "lib/crypto_keys.py"):
            result = council.plan(self.fixture((name,)))
            self.assertEqual(list(result["specialists"]), ["generalist", "reliability", "mvp", "security"])
            self.assertEqual(result["specialists"]["security"], "pr-review-security-v2")
        self.assertEqual(len(council.plan(self.fixture(("src/auth/token.py", "infra/iam/policy.yaml")))["specialists"]), 4)

    def snapshot(self, spec):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        (root / "input").mkdir(mode=0o700)
        (root / "snapshot").mkdir(mode=0o700)
        (root / "snapshot/diff.patch").write_bytes(DIFF)
        (root / "snapshot/diff.patch").chmod(0o400)
        manifest = root / "input/identity.json"
        manifest.write_text(json.dumps({key: spec[key] for key in
                                        ("operation_id", "head_sha", "base_sha", "diff_digest",
                                         "context_digest", "artifact_digest", "changed_paths")}))
        manifest.chmod(0o400)
        (root / "input/context.json").write_bytes(CONTEXT)
        (root / "input/context.json").chmod(0o400)
        return root

    def test_role_graph_is_idempotent_and_synthesis_waits_for_all_specialists(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.executescript("CREATE TABLE tasks(id TEXT PRIMARY KEY,idempotency_key TEXT,assignee TEXT,body TEXT);"
                           "CREATE TABLE task_links(parent_id TEXT,child_id TEXT);")
        calls = []
        class KB:
            @staticmethod
            def create_task(conn, **params):
                calls.append(params)
                task_id = "t_%08x" % (conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] + 1)
                conn.execute("INSERT INTO tasks VALUES (?,?,?,?)", (task_id, params["idempotency_key"],
                                                                    params["assignee"], params["body"]))
                for parent in params.get("parents", ()):
                    conn.execute("INSERT INTO task_links VALUES (?,?)", (parent, task_id))
                conn.commit()
                return task_id
        spec = council.plan(self.fixture(("src/auth/token.py",)))
        root = self.snapshot(spec)
        first = council.setup(KB, conn, spec, root)
        self.assertEqual(first, council.setup(KB, conn, spec, root))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 5)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0], 4)
        self.assertEqual(set(first), {"generalist", "reliability", "mvp", "security", "synthesis"})
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(p["workspace_kind"] == "dir" and
                            pathlib.Path(p["workspace_path"]).parent == root / "workspaces" and
                            p["board"] == council.BOARD and p["provider_override"] == "anthropic" and
                            p["max_retries"] == 0 for p in calls))
        for item in calls:
            directory = pathlib.Path(item["workspace_path"])
            self.assertEqual({p.name for p in directory.iterdir()}, {".council-tools.json"})
            self.assertEqual((directory / ".council-tools.json").stat().st_mode & 0o777, 0o440)

    def test_existing_restricted_snapshot_tool_accepts_only_bound_workspace(self):
        source = ROOT / "bin/hermes-council-tools"
        tool_loader = importlib.machinery.SourceFileLoader("council_tools_fixture", str(source))
        tool_spec = importlib.util.spec_from_loader(tool_loader.name, tool_loader)
        tool = importlib.util.module_from_spec(tool_spec)
        tool_loader.exec_module(tool)
        spec = council.plan(self.fixture())
        root = self.snapshot(spec).resolve()
        conn = sqlite3.connect(":memory:"); self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.executescript("CREATE TABLE tasks(id TEXT,idempotency_key TEXT,assignee TEXT,body TEXT);"
                           "CREATE TABLE task_links(parent_id TEXT,child_id TEXT);")
        class KB:
            @staticmethod
            def create_task(conn, **params):
                task_id = "t_%08x" % (conn.execute("SELECT count(*) FROM tasks").fetchone()[0] + 1)
                conn.execute("INSERT INTO tasks VALUES (?,?,?,?)",
                             (task_id, params["idempotency_key"], params["assignee"], params["body"]))
                for parent in params.get("parents", ()):conn.execute("INSERT INTO task_links VALUES (?,?)",(parent,task_id))
                conn.commit();return task_id
        ids = council.setup(KB, conn, spec, root)
        db = root / "board.db";db.write_bytes(b"")
        env = {"COUNCIL_TASK_ID": ids["generalist"], "COUNCIL_RUN_ID": "1", "COUNCIL_CLAIM_LOCK": "test:1",
               "COUNCIL_BOARD": council.BOARD, "COUNCIL_DB": str(db),
               "COUNCIL_WORKSPACE": str(root / "workspaces/generalist"),
               "COUNCIL_SNAPSHOT_ROOT": str(root / "snapshot"), "COUNCIL_WORKFLOW_ROOT": str(root / "input"),
               "COUNCIL_PROFILE": "pr-review-generalist-v2", "COUNCIL_TOOLS_PYTHON": sys.executable}
        with mock.patch.dict(os.environ, env, clear=True):
            roots = tool.safe_binding(tool.validated_aliases())
        self.assertEqual(roots, {"snapshot":root / "snapshot", "input":root / "input"})

    def test_snapshot_writer_reuses_exact_bytes_and_refuses_drift(self):
        spec = council.plan(self.fixture())
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = pathlib.Path(temp.name) / "operation"
        council.prepare_snapshot(root, spec, DIFF, CONTEXT)
        council.prepare_snapshot(root, spec, DIFF, CONTEXT)
        self.assertEqual((root / "snapshot/diff.patch").read_bytes(), DIFF)
        with self.assertRaises(ValueError):
            council.prepare_snapshot(root, spec, b"different", CONTEXT)
        (root / "snapshot/diff.patch").chmod(0o600)
        with self.assertRaises(ValueError):
            council.setup(object(), sqlite3.connect(":memory:"), spec, root)

    def test_unverified_snapshot_never_creates_cards(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        spec = council.plan(self.fixture())
        root = self.snapshot(spec)
        (root / "snapshot/diff.patch").chmod(0o600)
        (root / "snapshot/diff.patch").write_bytes(b"different synthetic diff")
        (root / "snapshot/diff.patch").chmod(0o400)
        with self.assertRaises(ValueError):
            council.setup(object(), conn, spec, root)

    def test_admission_replays_council_and_preserves_prior_legacy_owner(self):
        source=ROOT/'scripts/hermes-pr-kanban-enqueue.py'
        loader=importlib.machinery.SourceFileLoader('enqueue_admission',str(source))
        enqueue=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader))
        loader.exec_module(enqueue)
        class GitHub:
            def pr(self,*_):return {'state':'open','head_sha':'a'*40,'base_sha':'b'*40,
                                    'changed_files':1,'body':'synthetic goal'}
            def files(self,*_):return [{'filename':'src/a.py','patch':'+synthetic change'}]
            def diff(self,*_):return DIFF
            def reviews(self,*_):return []
            def comments(self,*_):return []
            def checks(self,*_):return []
        conn=sqlite3.connect(':memory:');self.addCleanup(conn.close)
        conn.row_factory=sqlite3.Row
        conn.executescript('CREATE TABLE tasks(id TEXT,idempotency_key TEXT,assignee TEXT,body TEXT);'
                           'CREATE TABLE task_links(parent_id TEXT,child_id TEXT);')
        class KB:
            @staticmethod
            def create_task(conn,**params):
                task_id='t_%08x'%(conn.execute('SELECT count(*) FROM tasks').fetchone()[0]+1)
                conn.execute('INSERT INTO tasks VALUES (?,?,?,?)',
                             (task_id,params['idempotency_key'],params['assignee'],params['body']))
                for parent in params.get('parents',()):conn.execute('INSERT INTO task_links VALUES (?,?)',(parent,task_id))
                conn.commit();return task_id
        fixture=self.fixture()
        request={'operation_id':fixture['operation_id'],'repo':fixture['repo'],'number':fixture['number'],
                 'head_sha':fixture['head_sha'],'url':'https://github.com/example/repo/pull/17','title':'synthetic'}
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp).resolve()
            first=council.admit(KB,conn,enqueue,GitHub(),root,request)
            self.assertEqual(first['route'],'council')
            self.assertEqual(first,council.admit(KB,conn,enqueue,GitHub(),root,request))
            self.assertEqual(conn.execute('SELECT count(*) FROM tasks').fetchone()[0],4)
            operation=root/request['operation_id']
            (operation/'snapshot/diff.patch').chmod(0o600)
            with self.assertRaises(ValueError):council.admit(KB,conn,enqueue,GitHub(),root,request)
            self.assertEqual(conn.execute('SELECT count(*) FROM tasks').fetchone()[0],4)
            manifest=json.loads((operation/'input/identity.json').read_text())
            receipt={'status':'verified','operation_id':request['operation_id'],
                     'head_sha':'a'*40,'base_sha':'b'*40,'artifact_digest':manifest['artifact_digest'],
                     'diff_digest':manifest['diff_digest'],'context_digest':manifest['context_digest'],
                     'task_id':first['task_id'],'review_id':7}
            council._immutable_file(operation/'review-outcome.json',json.dumps(receipt).encode())
            (operation/'input/identity.json').unlink()
            with self.assertRaisesRegex(ValueError,'cleanup incomplete'):
                council.admit(KB,conn,enqueue,GitHub(),root,request)
            (operation/'snapshot/diff.patch').unlink()
            (operation/'input/context.json').unlink()
            self.assertEqual(council.admit(KB,conn,enqueue,GitHub(),root,request),
                             {**first,'status':'done'})
            self.assertEqual(conn.execute('SELECT count(*) FROM tasks').fetchone()[0],4)
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp).resolve()
            workspace=enqueue.safe_dir(root/request['operation_id'],True)
            enqueue.immutable(workspace/'request.json',enqueue.canonical(request))
            self.assertEqual(council.admit(KB,conn,enqueue,GitHub(),root,request),{'route':'legacy'})

    def test_completed_handoffs_are_read_from_one_run_per_bound_profile(self):
        from types import SimpleNamespace
        spec=council.plan(self.fixture())
        records={}
        for role,profile in [*spec['specialists'].items(),('synthesis',spec['synthesis']['profile'])]:
            metadata={'operation_id':spec['operation_id'],'artifact_digest':spec['artifact_digest'],
                      'role':role,'verdict':'approve' if role=='synthesis' else 'clear','findings':[]}
            records[role]=(SimpleNamespace(status='done',assignee=profile),
                           [SimpleNamespace(outcome='completed',profile=profile,metadata=metadata)])
        class KB:
            def get_task(self,conn,id):return records[id][0]
            def list_runs(self,conn,id):return records[id][1]
        output=council.handoffs(KB(),None,spec,{role:role for role in records})
        self.assertEqual(output['synthesis']['verdict'],'approve')
        records['mvp'][1][0].profile='pr-review-v1'
        with self.assertRaises(ValueError):council.handoffs(KB(),None,spec,{role:role for role in records})
        records['mvp'][1][0].profile=spec['specialists']['mvp']
        records['mvp'][1].append(records['mvp'][1][0])
        with self.assertRaises(ValueError):council.handoffs(KB(),None,spec,{role:role for role in records})

    def test_snapshot_collector_rejects_rollover_truncation_or_missing_patch(self):
        class GitHub:
            def __init__(self):
                self.head='a'*40
                self.files_data=[{'filename':'src/worker.py','patch':'@@ -1 +1 @@\n-old\n+new'}]
            def pr(self,*_):return {'state':'open','head_sha':self.head,'base_sha':'b'*40,
                                    'changed_files':1,'body':'Synthetic goal'}
            def diff(self,*_):return b'diff --git a/src/worker.py b/src/worker.py\n@@ -1 +1 @@\n-old\n+new\n'
            def files(self,*_):return self.files_data
            def reviews(self,*_):return []
            def comments(self,*_):return []
            def checks(self,*_):return []
        gh=GitHub()
        request,diff,context=council.collect(gh,'example/repo',17,'a'*40)
        self.assertEqual(request['diff_digest'],hashlib.sha256(diff).hexdigest())
        self.assertEqual(request['context_digest'],hashlib.sha256(context).hexdigest())
        self.assertEqual(request['changed_paths'],['src/worker.py'])
        self.assertEqual(json.loads(context)['intent'],'Synthetic goal')
        gh.files_data[0].pop('patch')
        with self.assertRaises(ValueError):council.collect(gh,'example/repo',17,'a'*40)
        gh.files_data[0]['patch']='x';gh.head='c'*40
        with self.assertRaises(ValueError):council.collect(gh,'example/repo',17,'a'*40)

    def test_exact_request_file_owner_fences_legacy_and_council(self):
        source = ROOT / 'scripts/hermes-pr-kanban-enqueue.py'
        load = importlib.machinery.SourceFileLoader('enqueue_for_council', str(source))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(load.name, load))
        load.exec_module(module)
        spec = council.plan(self.fixture())
        request = {'operation_id':spec['operation_id'], 'repo':'example/repo', 'number':17,
                   'head_sha':spec['head_sha'], 'url':'https://github.com/example/repo/pull/17', 'title':'Synthetic'}
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            self.assertEqual(council.claim(module, root, spec, request), 'council')
            self.assertEqual(council.claim(module, root, spec, request), 'council')
            council.prepare_snapshot(root/spec['operation_id'],spec,DIFF,CONTEXT)
            with self.assertRaises(ValueError):
                council.claim(module, root, {**spec,'artifact_digest':'f'*64}, request)
            with self.assertRaises(ValueError):
                module.immutable(root/spec['operation_id']/'request.json', module.canonical(request))
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp).resolve()
            module.safe_dir(root/spec['operation_id'],True)
            module.immutable(root/spec['operation_id']/'request.json', module.canonical(request))
            self.assertEqual(council.claim(module, root, spec, request), 'legacy')
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp).resolve()
            workspace=module.safe_dir(root/spec['operation_id'],True)
            (workspace/'create-intent.json').write_text('uncertain legacy effect')
            with self.assertRaisesRegex(ValueError,'ambiguous prior review operation'):
                council.claim(module, root, spec, request)

    def test_artifact_identity_binds_paths_even_if_caller_reuses_diff_digest(self):
        first = council.plan(self.fixture(("src/a.py",)))
        second = council.plan(self.fixture(("src/b.py",)))
        self.assertNotEqual(first["artifact_digest"], second["artifact_digest"])

    def test_approve_allows_nits_but_not_required_changes_or_missing_roles(self):
        spec = council.plan(self.fixture())
        outputs = {role: {"operation_id": spec["operation_id"], "artifact_digest": spec["artifact_digest"],
                          "role": role, "verdict": "clear", "findings": []}
                   for role in spec["specialists"]}
        def synthesis(verdict, findings=()):
            return {"operation_id": spec["operation_id"], "artifact_digest": spec["artifact_digest"],
                    "role": "synthesis", "verdict": verdict, "findings": list(findings)}
        with self.assertRaises(ValueError):
            council.check_verdict(spec, outputs, "approve")
        self.assertEqual(council.check_verdict(spec, outputs, synthesis("approve")), "approve")
        self.assertEqual(council.check_verdict(spec, outputs, synthesis("needs-info")), "needs-info")
        outputs["reliability"]["verdict"] = "needs-info"
        with self.assertRaises(ValueError):
            council.check_verdict(spec, outputs, synthesis("approve"))
        self.assertEqual(council.check_verdict(spec, outputs, synthesis("needs-info")), "needs-info")
        outputs["reliability"]["verdict"] = "clear"
        outputs["mvp"]["findings"] = [{"severity": "nit", "required": False,
            "path": "src/worker.py", "line": 7, "claim": "simpler name", "evidence": "changed identifier",
            "suggestion": "consider a shorter name"}]
        self.assertEqual(council.check_verdict(spec, outputs,
                                               synthesis("approve", outputs["mvp"]["findings"])), "approve")
        outputs["mvp"]["findings"][0]["evidence"] = "x" * 2500
        with self.assertRaises(ValueError):
            council.check_verdict(spec, outputs, synthesis("approve"))
        outputs["mvp"]["findings"][0]["evidence"] = "changed identifier"
        outputs["reliability"]["verdict"] = "findings"
        outputs["reliability"]["findings"] = [{"severity": "major", "required": True,
            "path": "src/worker.py", "line": 8, "claim": "restart loses work", "evidence": "state not saved",
            "suggestion": "persist before I/O"}]
        with self.assertRaises(ValueError):
            council.check_verdict(spec, outputs, synthesis("approve", outputs["reliability"]["findings"]))
        with self.assertRaises(ValueError):
            council.check_verdict(spec, outputs, synthesis("request-changes"))
        both = outputs["reliability"]["findings"] + outputs["mvp"]["findings"]
        self.assertEqual(council.check_verdict(spec, outputs, synthesis("request-changes", both)), "request-changes")
        outputs.pop("reliability")
        with self.assertRaises(ValueError):
            council.check_verdict(spec, outputs, synthesis("approve"))

    def test_wrong_head_bad_path_or_incomplete_diff_is_not_admitted(self):
        for value in ({**self.fixture(), "head_sha": "d" * 40},
                      self.fixture(("../outside.py",)), self.fixture(("/tmp/private",)),
                      {**self.fixture(), "diff_digest": ""},
                      {**self.fixture(), "context_digest": ""},
                      {**self.fixture(), "changed_paths": []}):
            with self.assertRaises(ValueError):
                council.plan(value)


if __name__ == "__main__":
    unittest.main()
