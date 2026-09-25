#!/usr/bin/env python3
import argparse, copy, importlib.util, io, json, os, pathlib, sys, tempfile, unittest
from unittest import mock
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("direct_enqueue", ROOT / "scripts/hermes-pr-kanban-enqueue.py")
enqueue = importlib.util.module_from_spec(spec); spec.loader.exec_module(enqueue)
from hermes_direct_pr_journal import identity
class FakeCli:
    def __init__(self): self.boards={}; self.tasks={}; self.keys={}; self.commands=[]; self.envs=[]; self.seq=0; self.interrupt=False; self.attachments=[]; self.board_drift={}
    @staticmethod
    def event(kind,payload=None,run_id=None): return {"kind":kind,"payload":payload,"created_at":1,"run_id":run_id}
    @staticmethod
    def run(open=False): return {"id":1,"profile":"pr-review-v1","step_key":None,"status":"running" if open else "failed","outcome":None,
                "summary":None,"error":"boom","metadata":None,"worker_pid":123 if open else None,"started_at":1,"ended_at":None if open else 2}
    def board(self, slug, name, env):
        value={"slug":slug,"name":name,"description":"","icon":"","color":"","default_workdir":None,"project_id":None,"created_at":1,"archived":False,"db_path":str(pathlib.Path(env["HERMES_HOME"])/"kanban/boards"/slug/"kanban.db"),"is_current":False,"counts":{},"total":0}
        value.update(self.board_drift); return value
    def __call__(self, command, env, json_output=False, cwd=None):
        self.commands.append(command); self.envs.append((env,cwd))
        if command[1:4] == ["kanban","boards","list"]: return [self.board(slug,name,env) for slug,name in self.boards.items()]
        if command[1:4] == ["kanban","boards","create"]: self.boards[command[4]]=command[6]; return "created"
        board=command[command.index("--board")+1]; action=command[command.index("--board")+2]
        def value(flag): return command[command.index(flag)+1]
        if action == "list": raise AssertionError("task list is forbidden")
        if action == "create":
            key=(board,value("--idempotency-key"))
            if key in self.keys and self.tasks[self.keys[key]]["task"]["status"] != "archived": return self.tasks[self.keys[key]]["task"]
            self.seq += 1; task_id=f"t_{self.seq:08x}"; workspace=value("--workspace")[4:]; pathlib.Path(workspace,"create-intent.json").read_bytes()
            task={"id":task_id,"title":command[command.index("create")+1],"body":pathlib.Path(value("--body-file")).read_text(),
                  "assignee":value("--assignee"),"status":"blocked","priority":0,"tenant":value("--tenant"),"workspace_kind":"dir",
                  "workspace_path":workspace,"branch_name":None,"project_id":None,"created_by":value("--created-by"),"created_at":1,
                  "started_at":None,"completed_at":None,"result":None,"skills":[],"max_runtime_seconds":int(value("--max-runtime")),
                  "max_retries":int(value("--max-retries")),"model_override":value("--model"),"provider_override":value("--provider"),
                  "session_id":None,"workflow_template_id":None,"current_step_key":None,
                  "completion_contract":value("--completion-contract"),"last_failure_error":None}
            created={"assignee":task["assignee"],"status":"blocked","parents":[],"creator_task_id":None,"tenant":task["tenant"],
                     "workspace_kind":"dir","workspace_path":workspace,"branch_name":None,"project_id":None,"skills":None,
                     "goal_mode":None,"model_override":task["model_override"],"provider_override":task["provider_override"]}
            self.tasks[task_id]={"task":task,"latest_summary":None,"parents":[],"children":[],"comments":[],"runs":[],"events":[
                self.event("created",created),self.event("blocked",{"reason":"initial_status","status":"blocked","actor":"operator"})]}
            self.keys[key]=task_id
            if self.interrupt: self.interrupt=False; raise ValueError("interrupted create")
            return task
        task_id=command[command.index(action)+1]; shown=self.tasks[task_id]
        if action == "show": return shown
        if action == "attachments": return self.attachments
        if action == "assign": shown["task"]["assignee"]=None; shown["events"].append(self.event("assigned",{"assignee":None,"from":"pr-review-v1"}))
        elif action == "unblock": shown["task"].update(status="ready",last_failure_error=None); shown["events"].append(self.event("unblocked"))
        elif action == "request-review": shown["task"]["status"]="review"; shown["latest_summary"]=value("--summary"); shown["events"].append(self.event("review_requested",{"summary":value("--summary"),"implementer":None,"reviewer":None}))
        else: raise AssertionError(command)
        return "ok"
class EnqueueTest(unittest.TestCase):
    def fixture(self, kind="pr-review"):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup); root=pathlib.Path(temporary.name).resolve(); return argparse.Namespace(kind=kind,hermes_home=root/"home",hermes_bin=root/"hermes",workspace_root=root/"workspace"),FakeCli()
    def request(self, kind="pr-review", head="a", **changes):
        base={"repo":"owner/repo","number":7,"url":"https://github.com/owner/repo/pull/7","title":"Fix bug","head_sha":head*40}
        if kind == "pr-maintain": base.update(feedback_digest="1"*64,round=1)
        operation=identity(kind,base["repo"],base["number"],base["head_sha"],feedback_digest=base.get("feedback_digest")); return {"operation_id":operation["operation_id"],**base,**changes}
    def admit(self, args, cli, request=None):
        with mock.patch.object(enqueue,"run",side_effect=cli),mock.patch("sys.stdin",io.StringIO(json.dumps(request or self.request(args.kind)))): return enqueue.enqueue(args)
    @staticmethod
    def actions(cli): return ["boards.create" if command[1:4] == ["kanban","boards","create"] else command[command.index("--board")+2] for command in cli.commands if command[1:4] == ["kanban","boards","create"] or "--board" in command]
    def assert_inert(self,args,cli,message=None):
        cli.commands.clear()
        with self.assertRaisesRegex(ValueError,message or ".*"): self.admit(args,cli)
        self.assertFalse({"boards.create","create","assign","unblock","request-review"} & set(self.actions(cli)))
    @staticmethod
    def terminal(shown,status,assignee=None): shown["task"].update(status=status,assignee=assignee,completed_at=2 if status in {"done","archived"} else None,result="ok" if status in {"done","archived"} else None,last_failure_error=None)
    def test_board_separation_two_operations_replay_fixed_schema_and_no_task_list(self):
        args,cli=self.fixture(); first=self.admit(args,cli); replay=self.admit(args,cli); second=self.admit(args,cli,self.request(head="b"))
        maintain=copy.copy(args); maintain.kind="pr-maintain"; maintained=self.admit(maintain,cli,self.request("pr-maintain"))
        self.assertEqual(first,replay); self.assertEqual((len(cli.tasks),first["board"],second["board"]),(3,"pr-review","pr-review"))
        self.assertEqual((first["status"],maintained["status"]),("ready","ready")); self.assertNotIn("list",self.actions(cli))
        self.assertEqual(cli.boards,{"pr-review":"PR Review","pr-maintain":"PR Maintain"})
        cards=[item["task"] for item in cli.tasks.values()]; self.assertTrue(all(set(card) == enqueue.TASK_KEYS for card in cards)); self.assertEqual([c["max_runtime_seconds"] for c in cards],[1800,1800,2400])
        workspace=args.workspace_root/first["operation_id"]; self.assertEqual(tuple((workspace/name).stat().st_mode & 0o777 for name in ("request.json","create-intent.json","task-id.json")),(0o440,0o440,0o440))
        self.assertEqual(json.loads((workspace/"create-intent.json").read_text()),{"operation_id":first["operation_id"],"request_digest":enqueue.hashlib.sha256(enqueue.canonical(self.request())).hexdigest()}); self.assertEqual(json.loads((workspace/"task-id.json").read_text()),{"task_id":first["task_id"]})
        allowed={"HOME","HERMES_HOME","PATH","PYTHONUTF8","PYTHONDONTWRITEBYTECODE","HERMES_SAFE_MODE"}
        self.assertTrue(all(set(env) == allowed for env,_ in cli.envs))
    def test_lost_create_response_fails_closed_without_duplicate_on_every_replay(self):
        args,cli=self.fixture(); cli.interrupt=True
        with self.assertRaisesRegex(ValueError,"interrupted create"): self.admit(args,cli)
        for _ in range(2): self.assert_inert(args,cli,"unresolved create outcome"); self.assertEqual((len(cli.tasks),cli.commands),(1,[]))
    def test_initial_and_operational_routes_have_exact_readbacks(self):
        args,cli=self.fixture(); first=self.admit(args,cli); shown=cli.tasks[first["task_id"]]
        self.assertEqual((shown["task"]["status"],self.actions(cli).count("unblock")),("ready",1))
        shown["task"].update(status="blocked",last_failure_error="boom"); shown["runs"]=[cli.run()]; shown["events"].append(cli.event("failed",run_id=1)); cli.commands.clear()
        result=self.admit(args,cli)
        self.assertEqual((result["status"],shown["task"]["status"],shown["task"]["assignee"]),("review","review",None))
        self.assertEqual(self.actions(cli),["show","attachments","assign","show","unblock","show","request-review","show"])
        args,cli=self.fixture(); first=self.admit(args,cli); shown=cli.tasks[first["task_id"]]
        shown["task"].update(assignee=None); shown["events"].append(cli.event("assigned")); cli.commands.clear()
        self.assertEqual(self.admit(args,cli)["status"],"review")
    def test_manual_zero_run_block_routes_unassigned_review_without_dispatch_window(self):
        args,cli=self.fixture(); first=self.admit(args,cli); shown=cli.tasks[first["task_id"]]; shown["task"]["status"]="blocked"; shown["events"].append(cli.event("blocked")); cli.commands.clear()
        self.assertEqual((self.admit(args,cli)["status"],shown["task"]["assignee"],shown["runs"]),("review",None,[])); self.assertEqual(self.actions(cli),["show","attachments","assign","show","unblock","show","request-review","show"])
    def test_review_and_assigned_terminals_allow_multiple_closed_runs_but_open_runs_block(self):
        for status,output,assignee in (("review","review",None),("done","done",None),("done","done","pr-review-v1"),("archived","done",None),("archived","done","pr-review-v1")):
            with self.subTest(status=status,assignee=assignee):
                args,cli=self.fixture(); first=self.admit(args,cli); shown=cli.tasks[first["task_id"]]; self.terminal(shown,status,assignee); shown["runs"]=[cli.run(),dict(cli.run(),id=2)]; self.assertEqual(self.admit(args,cli)["status"],output)
        for case in ("assigned-review","open-review","open-done","open-archived","todo"):
            with self.subTest(case=case):
                args,cli=self.fixture(); first=self.admit(args,cli); shown=cli.tasks[first["task_id"]]
                if case == "todo": shown["task"]["status"]="todo"
                else: status=case.removeprefix("open-") if case.startswith("open-") else "review"; self.terminal(shown,status,"pr-review-v1" if case == "assigned-review" else None); shown["runs"]=[cli.run(case.startswith("open-"))]
                self.assert_inert(args,cli)
    def test_binding_tenant_schema_graph_attachment_and_history_drift_fail_inertly(self):
        for case in ("binding-mode","binding-link","binding-tampered","binding-foreign","tenant","task-schema","show-schema","parents","attachments","event-schema","event-run-id","run-id","history-bound","comment-schema","comment-oversize","comments-bound"):
            with self.subTest(case=case):
                args,cli=self.fixture(); first=self.admit(args,cli); shown=cli.tasks[first["task_id"]]; bind=args.workspace_root/first["operation_id"]/"task-id.json"
                if case == "binding-mode": bind.chmod(0o640)
                elif case == "binding-link": os.link(bind,bind.with_name("link"))
                elif case == "binding-tampered": bind.chmod(0o640); bind.write_text('{"task_id":"T"}'); bind.chmod(0o440)
                elif case == "binding-foreign":
                    foreign=copy.deepcopy(shown); foreign["task"].update(id="t_deadbeef",tenant="foreign"); cli.tasks["t_deadbeef"]=foreign
                    bind.chmod(0o640); bind.write_bytes(enqueue.canonical({"task_id":"t_deadbeef"})); bind.chmod(0o440)
                elif case == "tenant": shown["task"]["tenant"]="foreign"
                elif case == "task-schema": shown["task"]["drift"]=True
                elif case == "show-schema": shown["drift"]=True
                elif case == "parents": shown["parents"]=["foreign"]
                elif case == "attachments": cli.attachments=[{"id":"a"}]
                elif case == "event-schema": shown["events"][0]["drift"]=True
                elif case == "event-run-id": shown["events"][0]["run_id"]="1"
                elif case == "run-id": shown["runs"]=[dict(cli.run(),id="1")]
                elif case == "history-bound": shown["events"] *= 129
                elif case == "comment-schema": shown["comments"]=[{"author":None,"body":"x","created_at":1,"drift":True}]
                elif case == "comment-oversize": shown["comments"]=[{"author":None,"body":"x"*16001,"created_at":1}]
                else: shown["comments"]=[{"author":None,"body":"x","created_at":1}]*101
                self.assert_inert(args,cli)
    def test_board_drift_fails_before_task_mutation(self):
        cases=({"archived":True},{"project_id":"p"},{"db_path":"wrong"},{"drift":True},{"description":"x"},{"is_current":0},{"created_at":0},{"counts":[]},{"counts":{1:0}},{"counts":{"ready":True},"total":1},{"counts":{"ready":1},"total":0},{"total":-1})
        for drift in cases:
            with self.subTest(drift=drift):
                args,cli=self.fixture(); cli.boards["pr-review"]="PR Review"; cli.board_drift=drift; self.assert_inert(args,cli,"board"); self.assertEqual(cli.tasks,{})
    def test_atomic_request_and_intent_failure_retry_exactly(self):
        for target in ("request.json","create-intent.json"):
            with self.subTest(target=target):
                args,cli=self.fixture(); real=os.rename; failed=[]
                def rename(source,destination):
                    if pathlib.Path(destination).name == target and not failed: failed.append(True); raise OSError("fault")
                    return real(source,destination)
                with mock.patch.object(enqueue.os,"rename",side_effect=rename):
                    with self.assertRaisesRegex(OSError,"fault"): self.admit(args,cli)
                result=self.admit(args,cli); workspace=args.workspace_root/result["operation_id"]
                self.assertEqual(len(cli.tasks),1); self.assertFalse(any(path.name.startswith(".") for path in workspace.iterdir()))
                self.assertEqual((workspace/"task-id.json").read_bytes(),enqueue.canonical({"task_id":result["task_id"]}))
    def test_request_contract_and_exact_immutable_replay_reject_before_cli(self):
        cases=[{**self.request(),**change} for change in ({"operation_id":"pr-review-"+"0"*64},{"number":True},{"title":""},{"url":"https://example.test"},{"extra":"x"})]
        for request in cases:
            args,cli=self.fixture()
            with self.assertRaises(ValueError): self.admit(args,cli,request)
            self.assertEqual(cli.commands,[])
        args,cli=self.fixture(); request=self.request(); cli.boards["pr-review"]="Collision"
        with self.assertRaisesRegex(ValueError,"board collision"): self.admit(args,cli,request)
        workspace=args.workspace_root/request["operation_id"]; self.assertFalse((workspace/"create-intent.json").exists()); cli.boards["pr-review"]="PR Review"; self.admit(args,cli,request)
        changed={**request,"title":"Changed"}; cli.commands.clear()
        with self.assertRaisesRegex(ValueError,"existing immutable file differs"): self.admit(args,cli,changed)
        self.assertEqual(cli.commands,[])

if __name__ == "__main__": unittest.main()
