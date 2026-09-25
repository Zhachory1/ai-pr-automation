#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts/hermes-direct-pr-kanban-preflight.py"
SPEC = importlib.util.spec_from_file_location("direct_pr_preflight", PREFLIGHT)
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)
EXPECTED_COMMANDS = ["version","boards.create","boards.list","create","show","assign","show","unblock",
                     "show","dispatch","show","request-review","show","dispatch","show","complete","show"]
FAKE = r'''#!__PYTHON__
import json,os,pathlib,stat,subprocess,sys
LOG=pathlib.Path(__LOG__); FAULT=__FAULT__; a=sys.argv[1:]; home=pathlib.Path(os.environ["HERMES_HOME"]); state_path=home/"state.json"
state=json.loads(state_path.read_text()) if state_path.exists() else {}
managed=pathlib.Path(os.environ["HERMES_MANAGED_DIR"]); tmp=pathlib.Path(os.environ["TMPDIR"])
record={"args":a,"env":sorted(os.environ),"cwd":os.getcwd(),"path":os.environ["PATH"],"tmp":str(tmp),"managed":str(managed),"managed_entries":list(managed.iterdir()),"home_mode":stat.S_IMODE(pathlib.Path(os.environ["HOME"]).stat().st_mode),"hermes_mode":stat.S_IMODE(home.stat().st_mode),"root_mode":stat.S_IMODE(home.parent.stat().st_mode),"tmp_mode":stat.S_IMODE(tmp.stat().st_mode),"managed_mode":stat.S_IMODE(managed.stat().st_mode),"config":(home/"config.yaml").read_text(),"active_profile":(home/"active_profile").read_text(),"profiles":{name:(home/"profiles"/name/"config.yaml").read_text() for name in ("default","fixture-profile")},"hermes_bin":os.environ["HERMES_BIN"],"worker_mode":stat.S_IMODE(pathlib.Path(os.environ["HERMES_BIN"]).stat().st_mode),"state":{"status":state.get("status"),"assignee":state.get("assignee")}}
with LOG.open("a") as stream: stream.write(json.dumps(record)+"\n")
def emit(value): print(json.dumps(value))
def save(): state_path.write_text(json.dumps(state))
def value(flag): return a[a.index(flag)+1]
def event(kind,payload=None,run_id=None): state.setdefault("events",[]).append({"kind":kind,"payload":payload,"created_at":1,"run_id":run_id})
def board(slug,name,current,created,path):
 return {"slug":slug,"name":name,"description":"","icon":"","color":"","default_workdir":None,"project_id":None,"created_at":created,"archived":False,"db_path":str(path),"is_current":current,"counts":{},"total":0}
if a==["--version"]:
 print("Hermes Agent v0.21.4 · local ignored" if FAULT=="wrong_version" else "Hermes Agent v0.21.5 · local changed")
 sys.exit()
if a[:3]==["kanban","boards","create"]: state.update(board=a[3],name=value("--name")); save(); print("created"); sys.exit()
if a[:3]==["kanban","boards","list"]:
 if FAULT=="malformed": print("{"); sys.exit()
 if FAULT=="duplicate_json": print('{"x":1,"x":2}'); sys.exit()
 if FAULT=="oversized": print(json.dumps("x"*(2**20))); sys.exit()
 if FAULT=="nonzero_json": print("{}"); sys.exit(9)
 name="wrong" if FAULT=="board_mismatch" else state["name"]
 emit([board("default","Default",True,None,home/"kanban.db"),board(state["board"],name,False,1,home/"kanban/boards"/state["board"]/"kanban.db")]); sys.exit()
cmd=a[3]; task_id="t_deadbeef"
if cmd=="create":
 state.update({"id":task_id,"title":"direct-pr-preflight","body":"isolated fixture","assignee":"fixture-profile","status":"blocked","priority":0,"tenant":"preflight-tenant","workspace_kind":"dir","workspace_path":value("--workspace")[4:],"branch_name":None,"project_id":None,"created_by":"operator","created_at":1,"started_at":None,"completed_at":None,"result":None,"skills":[],"max_runtime_seconds":60,"max_retries":1,"model_override":None,"provider_override":None,"session_id":None,"workflow_template_id":None,"current_step_key":None,"completion_contract":"local-only","last_failure_error":None,"events":[],"runs":[],"latest_summary":None})
 event("created",{"assignee":"fixture-profile","status":"blocked","parents":[],"creator_task_id":None,"tenant":"preflight-tenant","workspace_kind":"dir","workspace_path":state["workspace_path"],"branch_name":None,"project_id":None,"skills":None,"goal_mode":None,"model_override":None,"provider_override":None}); event("blocked",{"reason":"initial_status","status":"blocked","actor":"operator"}); save(); emit({key:value for key,value in state.items() if key not in {"board","name","events","runs","latest_summary"}}); sys.exit()
if cmd=="assign": state["assignee"]=None; event("assigned",{"assignee":None,"from":"fixture-profile"})
elif cmd=="unblock": state["status"]="ready"; event("unblocked")
elif cmd=="request-review":
 state["status"]="review"; state["latest_summary"]=value("--summary"); event("review_requested",{"summary":state["latest_summary"],"implementer":None,"reviewer":None},1); state["runs"].append({"id":1,"profile":None,"step_key":None,"status":"review_requested","outcome":"review_requested","summary":state["latest_summary"],"error":None,"metadata":None,"worker_pid":None,"started_at":1,"ended_at":1})
elif cmd=="complete":
 state["status"]="done"; state["result"]=value("--result"); state["latest_summary"]=state["result"]; state["completed_at"]=1; event("completed",{"result_len":len(state["result"]),"summary":state["result"]},2); state["runs"].append({"id":2,"profile":None,"step_key":None,"status":"completed","outcome":"completed","summary":state["result"],"error":None,"metadata":None,"worker_pid":None,"started_at":1,"ended_at":1})
elif cmd=="dispatch":
 empty={key:[] for key in ("crashed","timed_out","stale","auto_blocked","reaped_terminal_workers","spawned","skipped_nonspawnable","skipped_per_profile_capped","auto_assigned_default","respawn_guarded","rate_limited")}; empty.update({"reclaimed":0,"promoted":0,"skipped_unassigned":[task_id],"skipped_locked":False,"memory_pressure":None})
 if FAULT=="auto_spawn": empty["spawned"]=[task_id]; empty["auto_assigned_default"]=[task_id]
 if FAULT=="hidden_spawn": subprocess.run([os.environ["HERMES_BIN"]],check=True)
 if FAULT=="hidden_dispatch_state": event("hidden_terminal",{},9); state["runs"].append({"id":9,"profile":None,"step_key":None,"status":"failed","outcome":"failed","summary":None,"error":"hidden","metadata":None,"worker_pid":None,"started_at":1,"ended_at":1}); save()
 emit(empty); sys.exit()
if cmd=="show":
 task={key:value for key,value in state.items() if key not in {"board","name","events","runs","latest_summary"}}
 if FAULT=="assigned_review" and state["status"]=="review": task["assignee"]="fixture-profile"
 if FAULT=="lingering_claim" and state["status"]=="done": task["claim_lock"]="live"
 events=[item for item in state["events"] if not (FAULT=="missing_review_event" and item["kind"]=="review_requested")]
 emit({"task":task,"latest_summary":state["latest_summary"],"parents":[],"children":[],"comments":[],"events":events,"runs":state["runs"]}); sys.exit()
save(); print("ok")
'''


class DirectPrKanbanPreflightTest(unittest.TestCase):
    def fake(self, root, fault=""):
        log = root/"calls.jsonl"; binary = root/"hermes"
        binary.write_text(FAKE.replace("__PYTHON__", sys.executable).replace("__LOG__", repr(str(log))).replace("__FAULT__", repr(fault))); binary.chmod(0o755)
        return binary, log

    def run_preflight(self, binary, config="{}\n", managed_config=None, managed_kind="directory"):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td); path = root/"config.yaml"; path.write_text(config); managed = root/"managed"
            if managed_kind == "directory": managed.mkdir()
            elif managed_kind == "file": managed.write_text("not a directory")
            elif managed_kind == "symlink":
                target = root/"managed-target"; target.mkdir(); managed.symlink_to(target, target_is_directory=True)
            if managed_config is not None: (managed/"config.yaml").write_text(managed_config)
            return subprocess.run([sys.executable, str(PREFLIGHT), "--hermes-bin", str(binary), "--config", str(path), "--managed-dir", str(managed)], capture_output=True, text=True)

    def test_happy_path_records_exact_commands_and_isolated_state(self):
        with tempfile.TemporaryDirectory() as td:
            binary, log = self.fake(pathlib.Path(td)); completed = self.run_preflight(binary)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"ready":True,"version":"0.21.5","commands":EXPECTED_COMMANDS,"writes":6,"dispatch_spawns":0,"network_policy":"sandbox-deny"})
        self.assertEqual(len(calls), len(EXPECTED_COMMANDS)); board = calls[1]["args"][3]; task = "t_deadbeef"
        expected = [["--version"],["kanban","boards","create",board,"--name",calls[1]["args"][5]],["kanban","boards","list","--all","--json"],calls[3]["args"],
            *[["kanban","--board",board,command,*([] if command == "dispatch" else [task]),*tail] for command,tail in (("show",["--json"]),("assign",["none"]),("show",["--json"]),("unblock",[]),("show",["--json"]),("dispatch",["--max","1","--json"]),("show",["--json"]),("request-review",["--summary","Bounded isolated preflight review."]),("show",["--json"]),("dispatch",["--max","1","--json"]),("show",["--json"]),("complete",["--result","Isolated preflight complete."]),("show",["--json"]))]]
        self.assertEqual([call["args"] for call in calls], expected)
        allowed = {"HERMES_BIN","HERMES_HOME","HERMES_MANAGED_DIR","HERMES_SAFE_MODE","HOME","PATH","PYTHONDONTWRITEBYTECODE","PYTHONUTF8","TMPDIR"}; injected = {"LC_CTYPE","__CF_USER_TEXT_ENCODING"}
        for call in calls:
            self.assertEqual(set(call["env"])-injected, allowed); self.assertLessEqual(set(call["env"])-allowed, injected)
            self.assertEqual(call["path"], "/usr/bin:/bin"); self.assertEqual(pathlib.Path(call["cwd"]).resolve(), pathlib.Path(call["managed"]).parent.resolve()); self.assertEqual(pathlib.Path(call["tmp"]).resolve(), (pathlib.Path(call["cwd"])/"tmp").resolve()); self.assertEqual(call["managed_entries"], [])
            self.assertTrue(all(call[key] == 0o700 for key in ("home_mode","hermes_mode","root_mode","tmp_mode","managed_mode")))
            self.assertEqual(call["config"], "updates:\n  check: false\nkanban:\n  default_assignee: null\n")
            self.assertEqual(call["active_profile"], "default\n"); self.assertEqual(call["profiles"], {"default":call["config"],"fixture-profile":call["config"]}); self.assertEqual(pathlib.Path(call["hermes_bin"]).name, "worker-sentinel"); self.assertEqual(call["worker_mode"], 0o700)
        self.assertEqual(calls[-1]["state"], {"status":"done","assignee":None})

    def test_contract_drift_fails_closed(self):
        faults = ("wrong_version","board_mismatch","auto_spawn","hidden_spawn","hidden_dispatch_state","assigned_review",
                  "missing_review_event","lingering_claim","malformed","duplicate_json","oversized","nonzero_json")
        for fault in faults:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as td:
                binary, _ = self.fake(pathlib.Path(td), fault); completed = self.run_preflight(binary)
                self.assertNotEqual(completed.returncode, 0); self.assertEqual(completed.stdout, ""); self.assertIn("spawned worker sentinel" if fault == "hidden_spawn" else "preflight failed", completed.stderr)

    def test_rejects_unsafe_effective_routing_config(self):
        cases = (("[]\n", None), ("kanban:\n  default_assignee: worker\n", None),
                 ("{}\n", "[]\n"), ("{}\n", "kanban:\n  default_assignee: worker\n"))
        for config, managed_config in cases:
            with self.subTest(config=config, managed_config=managed_config), tempfile.TemporaryDirectory() as td:
                binary, _ = self.fake(pathlib.Path(td))
                self.assertNotEqual(self.run_preflight(binary, config, managed_config).returncode, 0)

    def test_empty_or_missing_managed_config_passes(self):
        for managed_config, managed_kind in (("{}\n", "directory"), (None, "missing")):
            with self.subTest(managed_config=managed_config, managed_kind=managed_kind), tempfile.TemporaryDirectory() as td:
                binary, _ = self.fake(pathlib.Path(td))
                self.assertEqual(self.run_preflight(binary, managed_config=managed_config, managed_kind=managed_kind).returncode, 0)

    def test_rejects_existing_invalid_managed_dir(self):
        for managed_kind in ("file", "symlink"):
            with self.subTest(managed_kind=managed_kind), tempfile.TemporaryDirectory() as td:
                binary, _ = self.fake(pathlib.Path(td))
                self.assertNotEqual(self.run_preflight(binary, managed_kind=managed_kind).returncode, 0)

    def test_deadline_covers_wait_after_output_closes(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td); pid_path = root/"pid"
            child = f"import os,pathlib,time;pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()));os.close(1);os.close(2);time.sleep(10)"
            started = MODULE.time.monotonic()
            with mock.patch.object(MODULE, "TIMEOUT", 0.2):
                with self.assertRaises(subprocess.TimeoutExpired): MODULE.execute(pathlib.Path(sys.executable), ["-c", child], {}, root)
            self.assertLess(MODULE.time.monotonic()-started, 2)
            with self.assertRaises(ProcessLookupError): os.kill(int(pid_path.read_text()), 0)

    def test_process_group_cleanup_helper(self):
        process = mock.Mock(pid=123)
        with mock.patch.object(MODULE.os, "killpg") as killpg:
            MODULE.terminate_group(process)
        killpg.assert_called_once_with(123, signal.SIGKILL); process.wait.assert_called_once_with()

    def test_missing_sandbox_blocks(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(MODULE, "SANDBOX", pathlib.Path(td)/"missing"):
            config = pathlib.Path(td)/"config.yaml"; config.write_text("{}\n")
            with self.assertRaisesRegex(ValueError, "sandbox-exec unavailable"): MODULE.preflight("unused", config, pathlib.Path(td)/"managed")

    @unittest.skipUnless(os.environ.get("HERMES_TEST_BIN"), "set HERMES_TEST_BIN for pinned real CLI")
    def test_real_pinned_cli(self):
        completed = self.run_preflight(pathlib.Path(os.environ["HERMES_TEST_BIN"])); self.assertEqual(completed.returncode, 0, completed.stderr); report = json.loads(completed.stdout)
        self.assertTrue(report["ready"]); self.assertEqual(report["dispatch_spawns"], 0)


if __name__ == "__main__": unittest.main()
