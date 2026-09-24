#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
profiles=load("profiles_for_council",ROOT/"scripts/configure-hermes-kanban-profiles.py")
council=load("risk_council",ROOT/"scripts/hermes-kanban-risk-council.py")
import hermes_pr_safety_result as safety_result
CONTRACT=profiles.load_contract(ROOT/"agent-config/hermes/workflows/pr-risk-council-kanban.json")
CONTRACT_V2=profiles.load_contract(ROOT/"agent-config/hermes/workflows/pr-risk-council-kanban-v2.json")

class RiskCouncilTest(unittest.TestCase):
    def fixture(self,root,contract=CONTRACT):
        for name in list(sys.modules):
            if name=="hermes_cli" or name.startswith("hermes_cli."): sys.modules.pop(name)
        home=root/".hermes"; (home/"profiles").mkdir(parents=True)
        for value in contract["profiles"].values():
            source=home/"profiles"/value["source"]; (source/"skills/example").mkdir(parents=True)
            (source/"SOUL.md").write_text("# Specialist\n"); (source/"skills/example/SKILL.md").write_text("# Skill\n")
            (source/"config.yaml").write_text("{}\n"); (source/"profile.yaml").write_text("description: Specialist\n")
        profiles.apply(home,contract,os.getuid(),os.getgid())
        install=root/"install"; package=install/"hermes_cli"; package.mkdir(parents=True); (package/"__init__.py").write_text("")
        (package/"kanban_db_connect.py").write_text('''from contextlib import contextmanager
class Conn:
 def execute(self,sql,args=()):
  import hermes_cli.kanban_db as kb
  if 'idempotency_key' in sql:
   found=next((key for key,value in kb.tasks.items() if value.idempotency_key==args[0]),None)
   return Cursor({'id':found} if found else None)
  if 'COUNT(*)' in sql: return Cursor({'n':len(kb.tasks)})
  if 'task_links' in sql:
   task=kb.tasks.get(args[0]); return Cursor(rows=[{'parent_id':parent} for parent in (task.parents if task else [])])
  raise ValueError(sql)
class Cursor:
 def __init__(self,row=None,rows=None): self.row=row; self.rows=rows or []
 def fetchone(self): return self.row
 def fetchall(self): return self.rows
@contextmanager
def connect_closing(board=None): yield Conn()
''')
        (package/"kanban_db.py").write_text('''import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

@dataclass
class Run:
 id: int
 task_id: str
 profile: Optional[str]
 step_key: Optional[str]
 status: str
 claim_lock: Optional[str]
 claim_expires: Optional[int]
 worker_pid: Optional[int]
 max_runtime_seconds: Optional[int]
 last_heartbeat_at: Optional[int]
 started_at: int
 ended_at: Optional[int]
 outcome: Optional[str]
 summary: Optional[str]
 metadata: Optional[dict]
 error: Optional[str]

boards={}; tasks={}; comments={}; events={}; runs={}; seq=0
def board_exists(slug): return slug in boards
def create_board(slug,**kw): boards[slug]=kw; return {'slug':slug}
def remove_board(slug,archive=True): boards.pop(slug); return {'action':'archived'}
def create_task(conn,**kw):
 global seq; seq+=1; key=f't_{seq}'; parents=kw.get('parents') or []; tasks[key]=SimpleNamespace(status='todo' if parents else 'ready',title=kw.get('title'),assignee=kw.get('assignee'),created_by=kw.get('created_by'),priority=kw.get('priority',0),parents=parents,body=kw.get('body'),idempotency_key=kw.get('idempotency_key'),session_id=kw.get('session_id'),worker_pid=None,model_override=kw.get('model_override'),provider_override=kw.get('provider_override'),max_retries=kw.get('max_retries'),max_runtime_seconds=kw.get('max_runtime_seconds'),goal_mode=kw.get('goal_mode',False),goal_max_turns=kw.get('goal_max_turns'),skills=kw.get('skills'),reasoning_effort=kw.get('reasoning_effort'),workspace_kind=kw.get('workspace_kind'),workspace_path=kw.get('workspace_path'),branch_name=kw.get('branch_name'),project_id=kw.get('project_id'),tenant=kw.get('tenant'),completion_contract=kw.get('completion_contract')); comments[key]=[]; events[key]=[]; runs[key]=[]; return key
def get_task(conn,key): return tasks.get(key)
def list_runs(conn,key): return runs[key]
def list_comments(conn,key): return comments[key]
def list_attachments(conn,key): return []
def add_comment(conn,key,author,body): comments[key].append(SimpleNamespace(author=author,body=body))
def complete_task(conn,key,**kw):
 tasks[key].status='done'; tasks[key].worker_pid=None; ended=int(time.time()); runs[key].append(Run(id=len(runs[key])+1,task_id=key,profile=tasks[key].assignee,step_key=None,status='completed',claim_lock=None,claim_expires=None,worker_pid=None,max_runtime_seconds=tasks[key].max_runtime_seconds,last_heartbeat_at=None,started_at=ended-2,ended_at=ended,outcome='completed',summary=kw.get('summary'),metadata=kw.get('metadata'),error=None))
 for task in tasks.values():
  if task.status=='todo' and all(tasks[parent].status=='done' for parent in task.parents): task.status='ready'
 return True
''')
        return home,install

    def v2_request(self,root):
        snapshot=root/"snapshots/op"; snapshot.mkdir(parents=True)
        subprocess.run(["git","init","-q",snapshot],check=True)
        subprocess.run(["git","-C",snapshot,"config","user.email","test@example.com"],check=True)
        subprocess.run(["git","-C",snapshot,"config","user.name","Test"],check=True)
        (snapshot/"app.txt").write_text("old\n")
        subprocess.run(["git","-C",snapshot,"add","app.txt"],check=True)
        subprocess.run(["git","-C",snapshot,"commit","-qm","base"],check=True)
        base=subprocess.check_output(["git","-C",snapshot,"rev-parse","HEAD"],text=True).strip()
        (snapshot/"app.txt").write_text("new\n")
        subprocess.run(["git","-C",snapshot,"commit","-qam","head"],check=True)
        head=subprocess.check_output(["git","-C",snapshot,"rev-parse","HEAD"],text=True).strip()
        diff=subprocess.check_output(["git","-C",snapshot,"diff","--no-ext-diff",base,head])
        policy=root/"policy.md"; policy.write_text("pinned policy\n")
        request={"operation_id":"op","repo":"o/r","pr":7,"head_sha":head,"base_sha":base,
                 "diff_hash":__import__("hashlib").sha256(diff).hexdigest(),"policy_version":"v1",
                 "policy_digest":__import__("hashlib").sha256(policy.read_bytes()).hexdigest(),
                 "snapshot_path":str(snapshot),"policy_path":str(policy),"nonce":"a"*32}
        return request,snapshot,policy

    def v2_env(self,request,snapshot,policy):
        return {"PR_SAFETY_SNAPSHOT_ROOT":str(snapshot.parent),"PR_SAFETY_POLICY_PATH":str(policy),
                "PR_SAFETY_POLICY_VERSION":request["policy_version"],
                "PR_SAFETY_POLICY_DIGEST":request["policy_digest"]}

    def seed_usage(self,home,profile,session_id,*,source="kanban",model=None,cwd=None,started_at=None,
                   ended_at=None,input_tokens=1,output_tokens=2,cache_read_tokens=3,cache_write_tokens=4):
        path=home/"profiles"/profile/"state.db"
        with closing(sqlite3.connect(path)) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT, started_at REAL NOT NULL, ended_at REAL,
                input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0, cwd TEXT)""")
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (session_id,source,model,started_at,
                        started_at + 1 if ended_at is None and started_at is not None else ended_at,
                        input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,cwd)); db.commit()

    def v2_metadata(self,ctx,role):
        base={"workflow_id":ctx["workflow_id"],"artifact_digest":ctx["artifact_digest"]}
        if role!="synthesis":
            return {**base,"role":role,"verdict":"clear","claims":[],"evidence":[],"confidence":"high",
                    "dissent":[],"residual_risk":[]}
        return {**base,"verdict":"clear","intent":{},"findings":[],"coverage":{},"documentation":{},
                "observability":{},"incident":{"candidate":False,"changed_line_cause":False,
                "concrete_trigger":False,"severe_impact":False,"high_confidence_chain":False,
                "stop_rollback_or_page":False,"evidence":[]},"human_decisions_needed":[],"dissent":[],
                "residual_risk":[]}

    def metadata(self,role):
        base={"workflow_id":council.WORKFLOW_ID,"artifact_digest":council.ARTIFACT_DIGEST,"external_effects":0}
        if role=="verification": return {**base,"verdict":"changes_requested","material_findings":["tenant guard removed"],
            "consensus":["guard required"],"dissent":[],"evidence":["specialist handoffs"],
            "members_completed":list(council.SPECIALISTS),"members_failed":[]}
        return {**base,"role":role,"verdict":"findings","claims":["tenant guard removed"],"evidence":["supplied diff"],"confidence":"high","dissent":[],"residual_risk":[]}

    def test_graph_fan_in_verification_and_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            home,install=self.fixture(pathlib.Path(td)); setup=council.setup(home,install)
            self.assertFalse(setup["resumed"]); self.assertEqual(len(setup["tasks"]),5)
            first=council.status(home,install); self.assertEqual(first["task_count"],5)
            from hermes_cli import kanban_db as kb
            for role,profile in council.SPECIALISTS.items():
                task=setup["tasks"][role]
                comment="Reliability evidence with concrete failure mode and blast radius." if role=="reliability" else "progress"
                kb.add_comment(None,task,profile,comment)
                metadata={"worker_session_id":"fixture"} if role=="reliability" else self.metadata(role)
                kb.complete_task(None,task,metadata=metadata)
            mid=council.status(home,install)
            self.assertTrue(all(mid["tasks"][role]["verified"] for role in council.SPECIALISTS))
            self.assertEqual(mid["tasks"]["reliability"]["handoff_mode"],"comment")
            self.assertEqual(mid["tasks"]["security"]["handoff_mode"],"metadata")
            self.assertEqual(mid["tasks"]["verification"]["status"],"ready")
            task=setup["tasks"]["verification"]; kb.add_comment(None,task,council.VERIFIER,"synthesizing")
            verifier_metadata=self.metadata("verification")
            verifier_metadata["verdict"]="findings"
            verifier_metadata["members_completed"]=[setup["tasks"][role] for role in council.SPECIALISTS]
            kb.complete_task(None,task,metadata=verifier_metadata)
            final=council.status(home,install); self.assertTrue(final["terminal"]); self.assertTrue(final["verified"])
            self.assertTrue(council.cleanup(home,install)["archived"])

    def test_setup_resumes_and_detects_extra_task(self):
        with tempfile.TemporaryDirectory() as td:
            home,install=self.fixture(pathlib.Path(td)); first=council.setup(home,install)
            self.assertTrue(council.setup(home,install)["resumed"])
            from hermes_cli import kanban_db as kb
            kb.create_task(None,title="unexpected",assignee="council-reviewer")
            self.assertFalse(council.status(home,install)["verified"])
            with self.assertRaisesRegex(ValueError,"task count mismatch"): council.cleanup(home,install)

    def test_setup_recovers_from_state_before_board_creation(self):
        with tempfile.TemporaryDirectory() as td:
            home,install=self.fixture(pathlib.Path(td)); state=council.state_path(home); state.parent.mkdir(parents=True)
            council.atomic_json(state,{"schema_version":1,"phase":"setting_up","workflow_id":council.WORKFLOW_ID,
                "board":council.BOARD,"artifact_digest":council.ARTIFACT_DIGEST,"tasks":{},"created_at":1})
            result=council.setup(home,install)
            self.assertTrue(result["resumed"]); self.assertEqual(len(result["tasks"]),5)

    def test_canonical_metadata_recovers_known_tool_parameter_shape(self):
        from types import SimpleNamespace
        metadata=self.metadata("reliability")
        run=SimpleNamespace(metadata={"worker_session_id":"s"},summary="done</summary>\n<parameter name=\"metadata\">"+json.dumps(metadata))
        self.assertEqual(council.canonical_metadata(run),metadata)
        malformed=SimpleNamespace(metadata={"worker_session_id":"s"},summary="no metadata")
        self.assertEqual(council.canonical_metadata(malformed),{"worker_session_id":"s"})

    def test_metadata_contract_normalizes_optional_empty_lists_but_rejects_missing_evidence(self):
        specialist=self.metadata("security"); specialist.pop("dissent"); specialist.pop("residual_risk")
        self.assertTrue(council.valid_metadata(specialist,"security"))
        verifier=self.metadata("verification"); verifier.pop("dissent"); verifier.pop("members_failed")
        self.assertTrue(council.valid_metadata(verifier,"verification"))
        self.assertFalse(council.valid_metadata({"workflow_id":council.WORKFLOW_ID,
            "artifact_digest":council.ARTIFACT_DIGEST,"external_effects":0}, "security"))
        verifier=self.metadata("verification"); verifier["consensus"]=True
        self.assertFalse(council.valid_metadata(verifier,"verification"))

    def test_comment_fallback_requires_worker_session_id_key(self):
        # empty dict must NOT qualify for comment fallback (set({}) <= {...} was True; == is strict)
        self.assertFalse(council.valid_metadata({}, "reliability"))
        self.assertFalse(set({}) == {"worker_session_id"})
        self.assertTrue(set({"worker_session_id": "x"}) == {"worker_session_id"})

    def test_profile_policy_mismatch_fails_before_board(self):
        with tempfile.TemporaryDirectory() as td:
            home,install=self.fixture(pathlib.Path(td)); path=home/"profiles/council-security/config.yaml"
            path.write_text(path.read_text().replace("claude-haiku-4-5-20251001","claude-opus-5"))
            with self.assertRaisesRegex(ValueError,"policy mismatch"): council.setup(home,install)

    def test_v2_request_accepts_controller_valid_hidden_repository_name(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); request,snapshot,policy=self.v2_request(root)
            request["repo"]="ROKT/.github"
            workflow_root=root/"configured-workflows"; workflow_root.mkdir(); workflow_root.chmod(0o700)
            env={**self.v2_env(request,snapshot,policy),"PR_SAFETY_WORKFLOW_ROOT":str(workflow_root)}
            with mock.patch.dict(os.environ,env,clear=False):
                ctx=council.v2_context(root/".hermes",request,CONTRACT_V2)
            self.assertEqual(ctx["request"]["repo"],"ROKT/.github")

    def test_v2_dynamic_setup_resume_status_and_cleanup_uses_configured_workflow_root(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); request,snapshot,policy=self.v2_request(root)
            home,install=self.fixture(root,CONTRACT_V2)
            workflow_root=root/"configured-workflows"; workflow_root.mkdir(); workflow_root.chmod(0o700)
            env={**self.v2_env(request,snapshot,policy),"PR_SAFETY_WORKFLOW_ROOT":str(workflow_root)}
            with mock.patch.dict(os.environ,env,clear=False):
                setup=council.setup(home,install,request,CONTRACT_V2)
                self.assertFalse(setup["resumed"]); self.assertEqual(len(setup["tasks"]),5)
                resumed=council.setup(home,install,request,CONTRACT_V2); self.assertTrue(resumed["resumed"])
                ctx=council.v2_context(home,request,CONTRACT_V2)
                expected="pr-risk-council-"+__import__("hashlib").sha256(
                    f'{request["operation_id"]}:{request["nonce"]}'.encode()).hexdigest()[:32]
                self.assertEqual(ctx["workflow_id"],expected)
                retry=dict(request,nonce="b"*32)
                self.assertNotEqual(council.v2_context(home,retry,CONTRACT_V2)["workflow_id"],expected)
                self.assertEqual(ctx["root"].parent,workflow_root)
                self.assertFalse((home/"workflow-runs").exists())
                self.assertEqual(ctx["root"].stat().st_mode & 0o777,0o700)
                self.assertEqual((ctx["root"]/".council-tools.json").stat().st_mode & 0o777,0o440)
                self.assertEqual((ctx["input"]/"identity.json").stat().st_mode & 0o777,0o440)
                from hermes_cli import kanban_db as kb
                for role,profile in {**council.V2_SPECIALISTS,"synthesis":council.V2_SYNTHESIS}.items():
                    metadata=self.v2_metadata(ctx,role)
                    if role=="security": metadata["worker_session_id"]="forged-and-untrusted"
                    kb.complete_task(None,setup["tasks"][role],metadata=metadata)
                    run=kb.runs[setup["tasks"][role]][0]
                    self.seed_usage(home,profile,f"runtime-{role}",model=council.V2_MODELS[role],
                                    cwd=str(ctx["root"]),started_at=run.started_at+1)
                    self.assertIsNone(kb.tasks[setup["tasks"][role]].session_id)
                review_task=kb.tasks[setup["tasks"]["review"]]
                review_run=kb.runs[setup["tasks"]["review"]][0]
                review_task.worker_pid=123
                self.assertIsNone(council.status(home,install,request,CONTRACT_V2)["tasks"]["review"]["usage"])
                review_task.worker_pid=None; review_run.worker_pid=123
                self.assertIsNone(council.status(home,install,request,CONTRACT_V2)["tasks"]["review"]["usage"])
                review_run.worker_pid=None
                status=council.status(home,install,request,CONTRACT_V2)
                self.assertTrue(status["verified"]); self.assertEqual(status["package"]["verdict"],"clear")
                self.assertNotIn("worker_session_id",status["tasks"]["security"]["metadata"])
                self.assertEqual(status["usage"]["total_tokens"],15)
                security=kb.tasks[setup["tasks"]["security"]]
                security.model_override="claude-opus-5"
                self.assertFalse(council.status(home,install,request,CONTRACT_V2)["verified"])
                security.model_override=council.V2_MODELS["security"]
                synthesis=kb.tasks[setup["tasks"]["synthesis"]]; parents=synthesis.parents
                synthesis.parents=parents[:-1]
                self.assertFalse(council.status(home,install,request,CONTRACT_V2)["verified"])
                synthesis.parents=parents
                review=kb.tasks[setup["tasks"]["review"]]; body=review.body; review.body=body+" "
                self.assertFalse(council.status(home,install,request,CONTRACT_V2)["verified"])
                review.body=body; review.session_id="untrusted-origin-session"
                self.assertTrue(council.status(home,install,request,CONTRACT_V2)["verified"])
                self.assertTrue(council.cleanup(home,install,request,CONTRACT_V2)["archived"])
                self.assertFalse(ctx["root"].exists())
                self.assertFalse((home/"workflow-runs").exists())

    def test_v2_usage_matches_one_trusted_session_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); home,install=self.fixture(root,CONTRACT_V2)
            kb,_=council.modules(install)
            profile="council-security-v2"
            run=kb.Run(1,"task","council-security-v2",None,"completed",None,None,None,900,None,
                       1_000,1_100,"completed","done",{},None)
            candidates=(
                ("wrong-source",dict(source="cli",started_at=1_050)),
                ("wrong-model",dict(model="claude-sonnet-5",started_at=1_050)),
                ("out-of-window",dict(started_at=2_000)),
                ("match",dict(started_at=1_050,input_tokens=11,output_tokens=7,
                              cache_read_tokens=5,cache_write_tokens=3)),
            )
            defaults={"model":council.V2_MODELS["security"],"cwd":None}
            for session_id,values in candidates:
                self.seed_usage(home,profile,session_id,**{**defaults,**values})
            with mock.patch.object(safety_result.sqlite3,"connect",wraps=sqlite3.connect) as connect:
                usage=council.run_usage(home,profile,run,council.V2_MODELS["security"])
            self.assertEqual(usage,{"input_tokens":11,"output_tokens":7,"cache_read_tokens":5,
                                    "cache_write_tokens":3,"total_tokens":18})
            self.assertIn("?mode=ro",connect.call_args.args[0]); self.assertTrue(connect.call_args.kwargs["uri"])
            path=home/"profiles"/profile/"state.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE sessions SET ended_at = NULL WHERE id = 'match'"); db.commit()
            self.assertIsNone(council.run_usage(home,profile,run,council.V2_MODELS["security"]))
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE sessions SET ended_at = 1051 WHERE id = 'match'"); db.commit()
            self.seed_usage(home,profile,"second-match",**defaults,started_at=1_060)
            self.assertIsNone(council.run_usage(home,profile,run,council.V2_MODELS["security"]))
            with closing(sqlite3.connect(path)) as db:
                db.execute("DELETE FROM sessions WHERE id = 'second-match'")
                db.execute("UPDATE sessions SET input_tokens = -1 WHERE id = 'match'"); db.commit()
            self.assertIsNone(council.run_usage(home,profile,run,council.V2_MODELS["security"]))
            with closing(sqlite3.connect(path)) as db:
                db.execute("DELETE FROM sessions WHERE id = 'match'"); db.commit()
            self.assertIsNone(council.run_usage(home,profile,run,council.V2_MODELS["security"]))

    def test_v2_usage_supports_schema_without_model_or_cache_columns(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); home,install=self.fixture(root,CONTRACT_V2)
            kb,_=council.modules(install)
            profile="council-reviewer-v2"; path=home/"profiles"/profile/"state.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL, ended_at REAL, "
                           "input_tokens INTEGER, output_tokens INTEGER, cwd TEXT)")
                db.execute("INSERT INTO sessions VALUES ('one','kanban',1005,1006,2,3,NULL)"); db.commit()
            run=kb.Run(1,"task",profile,None,"completed",None,None,None,900,None,1000,1010,
                       "completed","done",{},None)
            self.assertEqual(council.run_usage(home,profile,run,council.V2_MODELS["review"]),
                             {"input_tokens":2,"output_tokens":3,"total_tokens":5})

    def test_v2_rejects_malformed_evidence_and_dissent(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); request,snapshot,policy=self.v2_request(root); home,_=self.fixture(root,CONTRACT_V2)
            with mock.patch.dict(os.environ,self.v2_env(request,snapshot,policy),clear=False):
                ctx=council.v2_context(home,request,CONTRACT_V2)
                lines=council.changed_lines(snapshot,request["base_sha"],request["head_sha"])
            metadata=self.v2_metadata(ctx,"security"); metadata["verdict"]="findings"
            self.assertFalse(council.valid_v2_metadata(metadata,"security",ctx,lines))
            metadata["claims"]=["changed behavior"]
            metadata["evidence"]=[{"path":"app.txt","line":1,"side":"new","quote":"new"}]
            self.assertTrue(council.valid_v2_metadata(metadata,"security",ctx,lines))
            malformed=json.loads(json.dumps(metadata)); malformed["evidence"][0]["quote"]="forged"
            self.assertFalse(council.valid_v2_metadata(malformed,"security",ctx,lines))
            malformed=json.loads(json.dumps(metadata)); malformed["dissent"]=[{"source_role":"security","claim":"x",
                "evidence":[],"disposition":"unresolved"}]
            self.assertFalse(council.valid_v2_metadata(malformed,"security",ctx,lines))
            synthesis=self.v2_metadata(ctx,"synthesis"); synthesis["unexpected"]=True
            self.assertFalse(council.valid_v2_metadata(synthesis,"synthesis",ctx,lines))

            evidence={"path":"app.txt","line":1,"side":"new","quote":"new"}
            incident=self.v2_metadata(ctx,"synthesis"); incident["verdict"]="incident_candidate"
            incident["incident"]["candidate"]=True
            for key in ("changed_line_cause","concrete_trigger","severe_impact","high_confidence_chain",
                        "stop_rollback_or_page"):
                incident["incident"][key]=True
            self.assertFalse(council.valid_v2_metadata(incident,"synthesis",ctx,lines))
            incident["incident"]["evidence"]=[evidence]
            self.assertTrue(council.valid_v2_metadata(incident,"synthesis",ctx,lines))
            incident["incident"]["candidate"]=False
            self.assertFalse(council.valid_v2_metadata(incident,"synthesis",ctx,lines))
            predicate=self.v2_metadata(ctx,"synthesis"); predicate["verdict"]="changes_requested"
            predicate["incident"]["changed_line_cause"]=True
            self.assertFalse(council.valid_v2_metadata(predicate,"synthesis",ctx,lines))
            predicate["incident"]["evidence"]=[dict(evidence,quote="forged")]
            self.assertFalse(council.valid_v2_metadata(predicate,"synthesis",ctx,lines))
            predicate["incident"]["evidence"]=[evidence]
            self.assertTrue(council.valid_v2_metadata(predicate,"synthesis",ctx,lines))

    def test_v2_policy_environment_fails_before_workflow_creation(self):
        cases=(
            ("missing",lambda env,root: env.pop("PR_SAFETY_POLICY_DIGEST")),
            ("version",lambda env,root: env.update(PR_SAFETY_POLICY_VERSION="v2")),
            ("digest",lambda env,root: env.update(PR_SAFETY_POLICY_DIGEST="b"*64)),
            ("path",lambda env,root: env.update(PR_SAFETY_POLICY_PATH=str(root/"other-policy.md"))),
            ("escape",lambda env,root: env.update(PR_SAFETY_SNAPSHOT_ROOT=str(root/"other-snapshots"))),
        )
        for name,mutate in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                root=pathlib.Path(td); request,snapshot,policy=self.v2_request(root)
                (root/"other-policy.md").write_text("other\n"); (root/"other-snapshots").mkdir()
                home,install=self.fixture(root,CONTRACT_V2); env=self.v2_env(request,snapshot,policy)
                env["PATH"]=os.environ.get("PATH",""); mutate(env,root)
                with mock.patch.dict(os.environ,env,clear=True), self.assertRaises(ValueError):
                    council.setup(home,install,request,CONTRACT_V2)
                self.assertFalse((home/"workflow-runs").exists())

    def test_v2_context_rejects_unsafe_configured_workflow_root(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); request,snapshot,policy=self.v2_request(root); home,_=self.fixture(root,CONTRACT_V2)
            unsafe=root/"unsafe-workflows"; unsafe.mkdir(); unsafe.chmod(0o755)
            target=root/"target-workflows"; target.mkdir(); target.chmod(0o700)
            alias=root/"workflow-alias"; alias.symlink_to(target, target_is_directory=True)
            for name,value in (("relative","relative"),("missing",str(root/"missing")),
                               ("mode",str(unsafe)),("symlink",str(alias))):
                with self.subTest(name=name):
                    env={**self.v2_env(request,snapshot,policy),"PR_SAFETY_WORKFLOW_ROOT":value}
                    with mock.patch.dict(os.environ,env,clear=False), self.assertRaisesRegex(ValueError,"workflow root"):
                        council.v2_context(home,request,CONTRACT_V2)

    def test_v2_profile_check_rejects_extra_config_and_mcp_json(self):
        for name,mutate in (
            ("extra config",lambda root: (root/"config.yaml").write_text(
                (root/"config.yaml").read_text()+"unexpected: true\n")),
            ("mcp json",lambda root: (root/"mcp.json").write_text("{}\n")),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                root=pathlib.Path(td); request,snapshot,policy=self.v2_request(root)
                home,install=self.fixture(root,CONTRACT_V2)
                mutate(home/"profiles/council-security-v2")
                with mock.patch.dict(os.environ,self.v2_env(request,snapshot,policy),clear=False), \
                        self.assertRaisesRegex(ValueError,"profile (policy mismatch|unavailable)"):
                    council.setup(home,install,request,CONTRACT_V2)
                self.assertFalse((home/"workflow-runs").exists())

    def test_changed_lines_uses_exact_machine_readable_paths_and_hunks(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot=pathlib.Path(td)/"repo"; snapshot.mkdir()
            subprocess.run(["git","init","-q",snapshot],check=True)
            subprocess.run(["git","-C",snapshot,"config","user.email","test@example.com"],check=True)
            subprocess.run(["git","-C",snapshot,"config","user.name","Test"],check=True)
            modified='space "quote" Ω\t.txt'; deleted="deleted\tfile.txt"; old='old "名".txt'; new="new name\t名.txt"
            (snapshot/modified).write_text("--old\n")
            (snapshot/deleted).write_text("--deleted\n")
            (snapshot/old).write_text("keep one\nrename old\nkeep three\n")
            subprocess.run(["git","-C",snapshot,"add","."],check=True)
            subprocess.run(["git","-C",snapshot,"commit","-qm","base"],check=True)
            base=subprocess.check_output(["git","-C",snapshot,"rev-parse","HEAD"],text=True).strip()
            (snapshot/modified).write_text("++new\n")
            (snapshot/deleted).unlink(); (snapshot/"added name.txt").write_text("++added\n")
            (snapshot/old).rename(snapshot/new); (snapshot/new).write_text("keep one\nrename new\nkeep three\n")
            subprocess.run(["git","-C",snapshot,"add","-A"],check=True)
            subprocess.run(["git","-C",snapshot,"commit","-qm","head"],check=True)
            head=subprocess.check_output(["git","-C",snapshot,"rev-parse","HEAD"],text=True).strip()
            lines=council.changed_lines(snapshot,base,head)
        expected={(modified,1,"old","--old"),(modified,1,"new","++new"),
                  (deleted,1,"old","--deleted"),("added name.txt",1,"new","++added"),
                  (old,2,"old","rename old"),(new,2,"new","rename new")}
        self.assertTrue(expected <= lines)

    def test_v2_dissent_union_is_deterministic_and_lossless(self):
        evidence=[]
        dissent={"source_role":"security","claim":"human must decide","evidence":evidence,
                 "disposition":"accepted","rationale":"specialist view"}
        finding_dissent={"source_role":"review","claim":"finding dissent","evidence":evidence,
                         "disposition":"unresolved","rationale":"needs decision"}
        finding_risk={"source_role":"reliability","claim":"finding risk","evidence":evidence,
                      "requires_human_decision":True}
        specialist={"dissent":[dissent],"residual_risk":[]}
        synthesis={"dissent":[],"residual_risk":[],
                   "findings":[{"dissent":[finding_dissent],"residual_risk":[finding_risk]}]}
        first=council.union_ledgers([specialist],synthesis)
        second=council.union_ledgers([specialist],synthesis)
        self.assertEqual(first,second)
        self.assertEqual(first[0][0]["source_role"],"review")
        self.assertEqual(first[0][1]["disposition"],"unresolved")
        self.assertEqual({item["claim"] for item in first[0]}, {"human must decide","finding dissent"})
        self.assertEqual(first[1],[finding_risk])

if __name__=="__main__": unittest.main()
