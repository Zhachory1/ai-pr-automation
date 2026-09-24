#!/usr/bin/env python3
import errno
import hashlib
import http.client
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import plistlib
import pwd
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from email.message import Message
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "bin/hermes-kanban-safety-bridge"
loader = importlib.machinery.SourceFileLoader("safety_bridge_test", str(BRIDGE_PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
bridge_module = importlib.util.module_from_spec(spec); loader.exec_module(bridge_module)

FAKE_COUNCIL = r'''
import hashlib,json,os,pathlib,shutil,sqlite3
from contextlib import closing,contextmanager
from types import SimpleNamespace
BOARD="pr-risk-council"; REQUEST_KEYS={"operation_id","repo","pr","head_sha","base_sha","diff_hash","policy_version","policy_digest","snapshot_path","policy_path","nonce"}
board=False; tasks={}; runs={}; events={}; status_mode="active"; unconfirmed=False
setup_fail_after=None; cleanup_archived=True; cleanup_removes_board=True
connector_busy_timeout=120000; resolved_busy_timeout=120000; termination_calls=[]; signal_calls=[]; archive_calls=[]; operations=[]; dead_pids=set()
dispatch_lock_available=True; dispatch_lock_held=False; mutation_lock_states=[]; read_lock_states=[]

def validate_v2_contract(value): return value

def v2_context(home,request,contract):
 workflow="pr-risk-council-"+hashlib.sha256(request["operation_id"].encode()).hexdigest()[:32]
 artifact=hashlib.sha256(json.dumps({"request":request,"contract_digest":hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(",",":")).encode()).hexdigest()},sort_keys=True,separators=(",",":")).encode()).hexdigest()
 root=pathlib.Path(home)/"workflow-runs"/workflow
 return {"workflow_id":workflow,"artifact_digest":artifact,"root":root,"request":request}

def setup_v2(home,install,request,contract):
 global board,setup_fail_after
 ctx=v2_context(home,request,contract); root=ctx["root"]; (root/"input").mkdir(parents=True,exist_ok=True)
 (root/"input/identity.json").write_text(json.dumps(request)); db=pathlib.Path(home)/"fake-kanban.db"
 if not board:
  tasks.clear(); runs.clear(); events.clear()
  if db.exists(): db.unlink()
 board=True
 with closing(sqlite3.connect(db)) as conn:
  conn.execute("PRAGMA journal_mode=DELETE"); conn.execute("PRAGMA busy_timeout=120000")
  conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY,status TEXT,worker_pid INTEGER,claim_lock TEXT,current_run_id INTEGER)")
  conn.execute("CREATE TABLE IF NOT EXISTS task_runs (id INTEGER PRIMARY KEY,task_id TEXT,status TEXT,claim_lock TEXT,worker_pid INTEGER,ended_at INTEGER)")
  for role in ("review","security","reliability","architecture","synthesis"):
   task_id="task-"+role; conn.execute("INSERT OR IGNORE INTO tasks VALUES (?,\"ready\",NULL,NULL,NULL)",(task_id,))
   tasks.setdefault(task_id,SimpleNamespace(status="ready",worker_pid=None,claim_lock=None,current_run_id=None))
   runs.setdefault(task_id,[]); events.setdefault(task_id,[])
   if setup_fail_after is not None and len(tasks)>=setup_fail_after:
    setup_fail_after=None; conn.commit(); raise ValueError("partial setup")
  conn.commit()
 task_ids={role:"task-"+role for role in ("review","security","reliability","architecture","synthesis")}
 state=root/"state.json"; state.write_text(json.dumps({"tasks":task_ids})); state.chmod(0o600)
 return {"tasks":task_ids}

def v2_state(ctx): return {"tasks":{role:"task-"+role for role in ("review","security","reliability","architecture","synthesis")}}

def status_v2(home,install,request,contract):
 ctx=v2_context(home,request,contract)
 if status_mode=="failed":
  tasks["task-security"].status="blocked"
 terminal=all(task.status in {"done","blocked","archived"} for task in tasks.values())
 verified=terminal and all(task.status=="done" for task in tasks.values())
 package={"workflow_id":ctx["workflow_id"],"artifact_digest":ctx["artifact_digest"],"verdict":"clear"} if verified else None
 return {"terminal":terminal,"verified":verified,"package":package,
         "tasks":{role:{"status":tasks["task-"+role].status} for role in ("review","security","reliability","architecture","synthesis")}}

def cleanup_v2(home,install,request,contract):
 global board
 if cleanup_removes_board: board=False
 return {"archived":cleanup_archived}

class KB:
 @staticmethod
 def board_exists(name): return board
 @staticmethod
 def kanban_db_path(name): return HOME/"fake-kanban.db"
 @staticmethod
 def get_task(conn,task_id): read_lock_states.append(dispatch_lock_held); return tasks.get(task_id)
 @staticmethod
 def list_runs(conn,task_id): read_lock_states.append(dispatch_lock_held); return runs.get(task_id,[])
 @staticmethod
 def list_events(conn,task_id): return events.get(task_id,[])
 @staticmethod
 def _terminate_reclaimed_worker(pid,claim_lock,signal_fn=None):
  was_dead=pid in dead_pids; termination_calls.append((pid,claim_lock,was_dead)); operations.append(("terminate",pid)); mutation_lock_states.append(dispatch_lock_held)
  result={"prev_pid":pid,"host_local":isinstance(claim_lock,str) and claim_lock.startswith("testhost:"),
          "termination_attempted":True,"terminated":False,"sigkill":False}
  if result["host_local"] and not unconfirmed:
   if not was_dead: signal_calls.append(pid)
   dead_pids.add(pid); result["terminated"]=True
  return result
 @staticmethod
 @contextmanager
 def write_txn(conn):
  conn.execute("BEGIN IMMEDIATE")
  try: yield; conn.commit()
  except Exception: conn.rollback(); raise
  finally:
   for task_id,worker_pid in conn.execute("SELECT id,worker_pid FROM tasks"):
    if task_id in tasks: tasks[task_id].worker_pid=worker_pid
   for run_id,worker_pid in conn.execute("SELECT id,worker_pid FROM task_runs"):
    for run in (item for values in runs.values() for item in values):
     if run.id==run_id: run.worker_pid=worker_pid
 @staticmethod
 def archive_task(conn,task_id):
  archive_calls.append(task_id); operations.append(("archive",task_id)); mutation_lock_states.append(dispatch_lock_held); task=tasks[task_id]
  if task.worker_pid is not None or any(run.worker_pid is not None for run in runs[task_id]):
   raise AssertionError("archive_task received non-null worker_pid")
  task.status="archived"; task.claim_lock=None; task.current_run_id=None
  for run in runs[task_id]:
   run.claim_lock=None; run.status="reclaimed"; run.ended_at=1
  return True
class KBC:
 DEFAULT_BUSY_TIMEOUT_MS=120000
 @staticmethod
 def connect(*args,**kwargs): raise AssertionError("direct connector unavailable")
 @staticmethod
 def _resolve_busy_timeout_ms(): return resolved_busy_timeout
 @staticmethod
 @contextmanager
 def _dispatch_tick_lock(db_path):
  global dispatch_lock_held
  acquired=dispatch_lock_available and not dispatch_lock_held
  if acquired: dispatch_lock_held=True
  try: yield acquired
  finally:
   if acquired: dispatch_lock_held=False
 @staticmethod
 @contextmanager
 def connect_closing(board=None):
  conn=sqlite3.connect(HOME/"fake-kanban.db",timeout=connector_busy_timeout/1000)
  conn.execute(f"PRAGMA busy_timeout={connector_busy_timeout}")
  try: yield conn
  finally: conn.close()

def modules(install): return KB,KBC
HOME=pathlib.Path("/")
'''


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temp.name)
        self.home = self.root / "home/.hermes"; self.install = self.root / "install"
        self.state = self.root / "state"; self.workflow = self.home / "workflow-runs"
        for path in (self.home, self.install, self.state, self.state/"workflows", self.workflow):
            path.mkdir(parents=True, exist_ok=True); path.chmod(0o700)
        self.contract = self.root / "contract.json"
        shutil.copy(ROOT/"agent-config/hermes/workflows/pr-risk-council-kanban-v2.json", self.contract)
        self.runtime = self.root / "native.env"; self.runtime.write_text("pin=one\n")
        self.fake = self.root / "hermes-kanban-risk-council.py"
        self.fake.write_text(FAKE_COUNCIL.replace('HOME=pathlib.Path("/")', f'HOME=pathlib.Path({str(self.home)!r})'))
        self.key = self.root / "key.json"; self.key.write_text(json.dumps(
            {"schema_version":1,"auth_generation":1,"key":"12"*32})+"\n"); self.key.chmod(0o600)
        contract = json.loads(self.contract.read_text())
        for name in contract["profiles"]:
            profile = self.home / "profiles" / name; (profile/"skills/example").mkdir(parents=True)
            for definition in ("SOUL.md","config.yaml","profile.yaml",".no-bundled-skills",".council-profile"):
                (profile/definition).write_text(definition + "\n")
            (profile/"skills/example/SKILL.md").write_text("skill\n")
        self.now = int(time.time())
        self.bridge = bridge_module.Bridge(home=self.home, install=self.install, state_root=self.state,
            workflow_root=self.workflow, key_file=self.key, contract_file=self.contract,
            runtime_contract=self.runtime, workflow_module=self.fake, min_free_bytes=1, now=lambda:self.now)
        self.bridge.council.HOME = self.home
        self.server = bridge_module.Server(("127.0.0.1",0), bridge_module.Handler, self.bridge,
                                           bridge_module.DEFAULT_HOST_HEADER)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.auth_counter = 0

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request_body(self, operation="operation-one", nonce="a"*32):
        return {"operation_id":operation,"repo":"owner/repo","pr":7,"head_sha":"1"*40,
                "base_sha":"2"*40,"diff_hash":"3"*64,"policy_version":"v1","policy_digest":"4"*64,
                "snapshot_path":str(self.root/"snapshots/op"),"policy_path":str(self.root/"policy.md"),
                "nonce":nonce}

    def headers(self, method, path, body, *, auth_nonce=None, timestamp=None, host=None, content_type=True):
        self.auth_counter += 1
        nonce = auth_nonce or f"{self.auth_counter:032x}"
        timestamp = self.now if timestamp is None else timestamp
        digest = bridge_module.body_digest(body)
        sig = bridge_module.signature(self.bridge.key, bridge_module.request_preimage(
            1,timestamp,nonce,digest,method,path))
        result = {"Host":host or bridge_module.DEFAULT_HOST_HEADER,"Content-Length":str(len(body)),
                  "X-Hermes-Auth-Generation":"1","X-Hermes-Timestamp":str(timestamp),
                  "X-Hermes-Nonce":nonce,"X-Hermes-Body-SHA256":digest,"X-Hermes-Signature":sig}
        if content_type: result["Content-Type"]="application/json"
        return result

    def call(self, method, path, value=None, *, raw=None, headers=None):
        body = raw if raw is not None else (bridge_module.canonical(value).encode() if value is not None else b"")
        headers = headers or self.headers(method,path,body,content_type=method=="POST")
        conn = http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        conn.request(method,path,body=body,headers=headers); response=conn.getresponse(); data=response.read()
        result=(response.status,json.loads(data),dict(response.getheaders()),data); conn.close(); return result

    def create(self, request=None):
        return self.call("POST","/v1/councils",request or self.request_body())

    def read_only_bridge(self):
        return bridge_module.Bridge(home=self.home,install=self.install,state_root=self.state,
            workflow_root=self.workflow,key_file=self.key,contract_file=self.contract,
            runtime_contract=self.runtime,workflow_module=self.fake,min_free_bytes=1,
            read_only=True,now=lambda:self.now)

    def action_body(self, workflow):
        state = self.bridge.load_state(workflow)
        return {"operation_id":state["operation_id"],"request_body_digest":state["request_body_digest"],
                "nonce":state["safety_request_nonce"]}

    def action(self, workflow, name, body=None):
        return self.call("POST",f"/v1/councils/{workflow}/{name}",body or self.action_body(workflow))

    def running_worker(self, task_id, pid=123, claim_lock="testhost:claim", run_id=1):
        task = self.bridge.council.tasks[task_id]
        task.status="running"; task.worker_pid=pid; task.claim_lock=claim_lock; task.current_run_id=run_id
        run=type("Run",(),{"id":run_id,"status":"running","claim_lock":claim_lock,
                           "worker_pid":pid,"ended_at":None})()
        self.bridge.council.runs[task_id]=[run]
        with closing(sqlite3.connect(self.home/"fake-kanban.db")) as conn:
            conn.execute("UPDATE tasks SET status='running',worker_pid=?,claim_lock=?,current_run_id=? WHERE id=?",
                         (pid,claim_lock,run_id,task_id))
            conn.execute("INSERT OR REPLACE INTO task_runs VALUES (?,?,'running',?,?,NULL)",
                         (run_id,task_id,claim_lock,pid)); conn.commit()
        return task,run

    def test_hmac_response_replay_nonce_and_health(self):
        path="/healthz"; body=b""; headers=self.headers("GET",path,body,content_type=False)
        status,value,response_headers,response_body=self.call("GET",path,raw=body,headers=headers)
        self.assertEqual((status,value["status"]),(200,"ok"))
        digest=bridge_module.body_digest(response_body)
        self.assertEqual(response_headers["X-Hermes-Body-SHA256"],digest)
        preimage=bridge_module.response_preimage(1,int(response_headers["X-Hermes-Timestamp"]),
            headers["X-Hermes-Nonce"],digest,"GET",path,200)
        self.assertEqual(response_headers["X-Hermes-Signature"],bridge_module.signature(self.bridge.key,preimage))
        replay=self.call("GET",path,raw=body,headers=headers)
        self.assertEqual(replay[0],409); self.assertEqual(replay[2]["X-Hermes-Nonce"],headers["X-Hermes-Nonce"])
        ledger=json.loads((self.state/"nonces.json").read_text())
        self.assertIn(headers["X-Hermes-Nonce"],ledger["nonces"])
        restarted=bridge_module.Bridge(home=self.home,install=self.install,state_root=self.state,
            workflow_root=self.workflow,key_file=self.key,contract_file=self.contract,
            runtime_contract=self.runtime,workflow_module=self.fake,min_free_bytes=1,now=lambda:self.now)
        message=Message()
        for key,value in headers.items(): message[key]=value
        with self.assertRaisesRegex(bridge_module.BridgeError,"replayed_nonce"):
            restarted.authenticate(message,"GET",path,body)

    def test_status_and_stop_reject_replayed_auth_nonce_and_fresh_stop_is_idempotent(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        status_path=f"/v1/councils/{workflow}"; empty=b""
        headers=self.headers("GET",status_path,empty,content_type=False)
        self.assertEqual(self.call("GET",status_path,raw=empty,headers=headers)[0],200)
        self.assertEqual(self.call("GET",status_path,raw=empty,headers=headers)[0],409)

        stop_path=status_path+"/stop"; body=bridge_module.canonical(self.action_body(workflow)).encode()
        headers=self.headers("POST",stop_path,body)
        first=self.call("POST",stop_path,raw=body,headers=headers)
        self.assertEqual((first[0],first[1]["phase"]),(200,"stopped"))
        self.assertEqual(self.call("POST",stop_path,raw=body,headers=headers)[0],409)
        fresh=self.call("POST",stop_path,raw=body)
        self.assertEqual((fresh[0],fresh[1]),(200,first[1]))

    def test_rejects_bad_hmac_generation_timestamp_host_origin_content_type_and_body(self):
        path="/healthz"; body=b""
        cases=[]
        bad=self.headers("GET",path,body,content_type=False); bad["X-Hermes-Signature"]="0"*64; cases.append((bad,401))
        bad=self.headers("GET",path,body,content_type=False); bad["X-Hermes-Auth-Generation"]="2"; cases.append((bad,401))
        cases.append((self.headers("GET",path,body,timestamp=self.now-61,content_type=False),401))
        cases.append((self.headers("GET",path,body,host="localhost:8766",content_type=False),400))
        bad=self.headers("GET",path,body,content_type=False); bad["Origin"]="https://example.test"; cases.append((bad,403))
        for headers,expected in cases: self.assertEqual(self.call("GET",path,raw=body,headers=headers)[0],expected)
        request=self.request_body(); raw=bridge_module.canonical(request).encode()
        self.assertEqual(self.call("POST","/v1/councils",raw=raw,
            headers=self.headers("POST","/v1/councils",raw,content_type=False))[0],415)
        headers=self.headers("POST","/v1/councils",b""); headers["Content-Length"]=str(bridge_module.BODY_LIMIT+1)
        self.assertEqual(self.call("POST","/v1/councils",raw=b"",headers=headers)[0],413)
        self.assertEqual(self.call("POST","/v1/councils",{"extra":True})[0],400)
        self.assertNotIn("Access-Control-Allow-Origin",self.call("OPTIONS","/v1/councils",{})[2])

    def test_create_exact_replay_conflict_busy_and_crash_resume(self):
        request=self.request_body(); raw=bridge_module.canonical(request).encode()
        original=self.bridge.council.setup_v2
        with mock.patch.object(self.bridge.council,"setup_v2",side_effect=ValueError("private input")):
            status,value,_,_=self.call("POST","/v1/councils",raw=raw)
        self.assertEqual((status,value),(422,{"error":"workflow_preflight_failed"}))
        workflow="pr-risk-council-"+hashlib.sha256(request["operation_id"].encode()).hexdigest()[:32]
        self.assertEqual(self.bridge.load_state(workflow)["phase"],"creating")
        self.bridge.council.setup_v2=original
        self.assertEqual(self.call("POST","/v1/councils",raw=raw)[0],200)
        self.assertEqual(self.bridge.load_state(workflow)["phase"],"active")
        changed=dict(request,nonce="b"*32)
        with mock.patch.object(self.bridge.council,"v2_context",side_effect=AssertionError("must not preflight")):
            self.assertEqual(self.create(changed)[0],409)
        self.assertEqual(self.create(self.request_body("operation-two"))[0],429)
        state=self.bridge.load_state(workflow)
        self.assertEqual(state["request_body_digest"],hashlib.sha256(raw).hexdigest())
        self.assertEqual(set(state["task_ids"]),{"review","security","reliability","architecture","synthesis"})

    def test_setup_crash_after_each_task_create_resumes_exact_graph(self):
        for created_count in range(1,6):
            with self.subTest(created_count=created_count):
                request=self.request_body(f"crash-after-{created_count}",nonce=f"{created_count:032x}")
                self.bridge.council.setup_fail_after=created_count
                status,value,_,_=self.create(request)
                self.assertEqual((status,value["error"]),(422,"workflow_preflight_failed"))
                self.assertEqual(len(self.bridge.council.tasks),created_count)
                status,value,_,_=self.create(request)
                self.assertEqual((status,value["phase"]),(200,"active"))
                self.assertEqual(value["task_ids"],{role:"task-"+role for role in
                    ("review","security","reliability","architecture","synthesis")})
                self.assertEqual(len(self.bridge.council.tasks),5)
                workflow=value["workflow_id"]
                self.action(workflow,"stop"); self.action(workflow,"archive")

    def test_failure_before_first_bridge_state_write_leaves_no_board_or_workflow_state(self):
        with mock.patch.object(self.bridge,"save_state",side_effect=OSError(errno.ENOSPC,"full")):
            status,value,_,_=self.create()
        self.assertEqual((status,value),(500,{"error":"internal_error"}))
        self.assertFalse(self.bridge.council.board)
        self.assertEqual(list((self.state/"workflows").glob("*.json")),[])

    def test_atomic_enospc_preserves_previous_state(self):
        target=self.root/"atomic.json"; bridge_module.atomic_json(target,{"old":True}); before=target.read_bytes()
        with mock.patch.object(bridge_module.os,"replace",side_effect=OSError(errno.ENOSPC,"full")):
            with self.assertRaises(OSError): bridge_module.atomic_json(target,{"new":True})
        self.assertEqual(target.read_bytes(),before); self.assertEqual(list(self.root.glob(".atomic.json.tmp-*")),[])

    def test_virgin_read_only_reconcile_does_not_require_or_create_nonce_ledger(self):
        self.bridge.nonce_file.unlink()
        with mock.patch.object(bridge_module,"load_module",return_value=self.bridge.council):
            report=self.read_only_bridge().reconcile_report()
        self.assertEqual(report["workflows"],[]); self.assertFalse(self.bridge.nonce_file.exists())

    def test_terminal_archive_tombstone_replay_and_new_create_reconcile(self):
        status,created,_,_=self.create(); self.assertEqual(status,201); workflow=created["workflow_id"]
        for task in self.bridge.council.tasks.values(): task.status="done"
        status,value,_,_=self.call("GET",f"/v1/councils/{workflow}")
        self.assertEqual((status,value["phase"]),(200,"terminal")); result_digest=value["result_digest"]
        status,value,_,_=self.action(workflow,"archive")
        self.assertEqual((status,value["phase"],value["result_package"]),(200,"archived",None))
        self.assertEqual(value["result_digest"],result_digest); self.assertFalse((self.workflow/workflow).exists())
        tombstone=self.bridge.load_state(workflow); self.assertNotIn("snapshot_path",tombstone["request_identity"])
        replay=self.action(workflow,"archive"); self.assertEqual((replay[0],replay[1]),(200,value))
        status,archived,_,_=self.call("GET",f"/v1/councils/{workflow}")
        self.assertEqual((status,archived["error"],archived["tombstone"]),(410,"archived_workflow",value))
        status,created,_,_=self.create(self.request_body("operation-two",nonce="b"*32))
        self.assertEqual(status,201); report=self.read_only_bridge().reconcile_report()
        archived_row=next(row for row in report["workflows"] if row["workflow_id"]==workflow)
        active_row=next(row for row in report["workflows"] if row["workflow_id"]==created["workflow_id"])
        self.assertEqual((archived_row["phase"],archived_row["mismatch"],archived_row["status"]),
                         ("archived",False,None))
        self.assertEqual((active_row["phase"],active_row["mismatch"]),("active",False))

    def test_archive_requires_confirmed_board_removal_and_resumes_after_removal(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        self.action(workflow,"stop"); root=self.workflow/workflow
        self.bridge.council.cleanup_archived=False; self.bridge.council.cleanup_removes_board=False
        status,value,_,_=self.action(workflow,"archive")
        self.assertEqual((status,value["error"]),(503,"archive_failed")); self.assertTrue(root.exists())
        self.bridge.council.cleanup_archived=True
        status,value,_,_=self.action(workflow,"archive")
        self.assertEqual((status,value["error"]),(503,"archive_failed")); self.assertTrue(root.exists())
        self.bridge.council.board=False
        state=self.bridge.load_state(workflow)
        self.assertEqual((state["phase"],state["archive_cleanup_confirmed"]),("archiving",False))
        status,value,_,_=self.action(workflow,"archive")
        self.assertEqual((status,value["phase"],value["archive_cleanup_confirmed"]),(200,"archived",True))
        self.assertFalse(root.exists())

    def test_archive_resumes_after_board_removal_before_confirmation_write(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        self.action(workflow,"stop"); root=self.workflow/workflow
        save_state=self.bridge.save_state
        def fail_confirmation(state):
            if state["phase"]=="archiving" and state["archive_cleanup_confirmed"]:
                raise OSError(errno.ENOSPC,"full")
            save_state(state)
        with mock.patch.object(self.bridge,"save_state",side_effect=fail_confirmation):
            status,value,_,_=self.action(workflow,"archive")
        self.assertEqual((status,value),(503,{"error":"archive_failed"}))
        state=self.bridge.load_state(workflow)
        self.assertEqual((state["phase"],state["archive_cleanup_confirmed"],self.bridge.council.board),
                         ("archiving",False,False))
        self.assertTrue(root.exists())
        status,value,_,_=self.action(workflow,"archive")
        self.assertEqual((status,value["phase"],value["archive_cleanup_confirmed"]),(200,"archived",True))
        self.assertFalse(root.exists())

    def test_stop_fails_closed_status_resumes_and_preserves_terminal_reason(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        task_id=next(iter(self.bridge.council.tasks)); task,_=self.running_worker(task_id)
        state=self.bridge.load_state(workflow); state["terminal_failure"]="expired"; self.bridge.save_state(state)
        self.bridge.council.unconfirmed=True
        status,value,_,_=self.action(workflow,"stop")
        self.assertEqual((status,value["error"]),(503,"worker_termination_unconfirmed"))
        self.assertEqual((self.bridge.load_state(workflow)["phase"],self.bridge.load_state(workflow)["terminal_failure"]),
                         ("stopping","expired"))
        self.assertEqual(self.bridge.council.archive_calls,[])
        self.bridge.council.unconfirmed=False
        status,value,_,_=self.call("GET",f"/v1/councils/{workflow}")
        self.assertEqual((status,value["phase"],value["terminal_failure"]),(200,"terminal","expired"))

    def test_stop_lock_unavailable_is_retryable_without_worker_mutation(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        task_id=next(iter(self.bridge.council.tasks)); task,_=self.running_worker(task_id)
        self.bridge.council.dispatch_lock_available=False
        status,value,_,_=self.action(workflow,"stop")
        self.assertEqual((status,value),(503,{"error":"dispatch_lock_unavailable"}))
        self.assertEqual(self.bridge.load_state(workflow)["phase"],"stopping")
        self.assertEqual((self.bridge.council.termination_calls,self.bridge.council.archive_calls),([],[]))
        self.assertEqual((task.status,task.worker_pid),("running",123))

    def test_stop_holds_dispatch_lock_through_reads_kill_archive_and_verification(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        task_id=next(iter(self.bridge.council.tasks)); self.running_worker(task_id)
        gateway_lock_results=[]; archive_task=self.bridge.council.KB.archive_task
        def archive_with_gateway_attempt(conn,current_task_id):
            with self.bridge.council.KBC._dispatch_tick_lock(
                    self.bridge.council.KB.kanban_db_path(self.bridge.council.BOARD)) as acquired:
                gateway_lock_results.append(acquired)
            return archive_task(conn,current_task_id)
        self.bridge.council.read_lock_states.clear()
        with mock.patch.object(self.bridge.council.KB,"archive_task",side_effect=archive_with_gateway_attempt):
            status,value,_,_=self.action(workflow,"stop")
        self.assertEqual((status,value["phase"]),(200,"stopped"))
        self.assertTrue(self.bridge.council.read_lock_states)
        self.assertTrue(all(self.bridge.council.read_lock_states))
        self.assertTrue(all(self.bridge.council.mutation_lock_states))
        self.assertEqual(gateway_lock_results,[False] * 5)

    def test_stop_crash_after_kill_before_pid_clear_retries_dead_pid_without_second_signal(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        task_id=next(iter(self.bridge.council.tasks)); task,_=self.running_worker(task_id)
        clear_worker_pids=self.bridge.clear_worker_pids; crashed=False
        def crash_once(*args):
            nonlocal crashed
            if not crashed:
                crashed=True; self.assertIn(123,self.bridge.council.dead_pids)
                raise OSError("crash before pid clear")
            return clear_worker_pids(*args)
        with mock.patch.object(self.bridge,"clear_worker_pids",side_effect=crash_once):
            status,value,_,_=self.action(workflow,"stop")
            self.assertEqual((status,value),(503,{"error":"worker_termination_unconfirmed"}))
            self.assertEqual((task.status,task.worker_pid),("running",123))
            status,value,_,_=self.action(workflow,"stop")
        self.assertEqual((status,value["phase"]),(200,"stopped"))
        self.assertEqual(self.bridge.council.termination_calls,
                         [(123,"testhost:claim",False),(123,"testhost:claim",True)])
        self.assertEqual(self.bridge.council.signal_calls,[123])
        self.assertEqual((task.status,task.worker_pid),("archived",None))

    def test_stop_crash_after_pid_clear_resumes_archive_without_termination(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        task_id=next(iter(self.bridge.council.tasks)); task,run=self.running_worker(task_id)
        archive_task=self.bridge.council.KB.archive_task; crashed=False
        def crash_once(conn,current_task_id):
            nonlocal crashed
            if not crashed:
                crashed=True; raise OSError("crash after pid clear")
            return archive_task(conn,current_task_id)
        with mock.patch.object(self.bridge.council.KB,"archive_task",side_effect=crash_once):
            status,value,_,_=self.action(workflow,"stop")
            self.assertEqual((status,value),(503,{"error":"worker_termination_unconfirmed"}))
            self.assertEqual((task.status,task.worker_pid,task.claim_lock),("running",None,"testhost:claim"))
            self.assertEqual((run.status,run.worker_pid,run.claim_lock),("running",None,"testhost:claim"))
            status,value,_,_=self.action(workflow,"stop")
        self.assertEqual((status,value["phase"]),(200,"stopped"))
        self.assertEqual(self.bridge.council.termination_calls,[(123,"testhost:claim",False)])
        self.assertEqual(self.bridge.council.signal_calls,[123])

    def test_stop_pid_clear_cas_mismatch_rolls_back_and_skips_archive(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        task_id=next(iter(self.bridge.council.tasks)); task,run=self.running_worker(task_id)
        terminate=self.bridge.council.KB._terminate_reclaimed_worker
        def change_pid_after_kill(pid,claim_lock):
            result=terminate(pid,claim_lock)
            with closing(sqlite3.connect(self.home/"fake-kanban.db")) as conn:
                conn.execute("UPDATE tasks SET worker_pid=999 WHERE id=?",(task_id,)); conn.commit()
            return result
        with mock.patch.object(self.bridge.council.KB,"_terminate_reclaimed_worker",
                               side_effect=change_pid_after_kill):
            status,value,_,_=self.action(workflow,"stop")
        self.assertEqual((status,value),(503,{"error":"worker_termination_unconfirmed"}))
        self.assertEqual((task.worker_pid,run.worker_pid),(999,123))
        self.assertEqual(self.bridge.council.archive_calls,[])

    def test_stop_kills_all_running_workers_before_first_archive(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        task_ids=list(self.bridge.council.tasks)[:2]
        for run_id,(task_id,pid) in enumerate(zip(task_ids,(123,456)),1):
            self.running_worker(task_id,pid,f"testhost:claim-{run_id}",run_id)
        status,value,_,_=self.action(workflow,"stop")
        self.assertEqual((status,value["phase"]),(200,"stopped"))
        first_archive=next(index for index,item in enumerate(self.bridge.council.operations) if item[0]=="archive")
        self.assertEqual(self.bridge.council.operations[:first_archive],[('terminate',123),('terminate',456)])
        self.assertEqual(self.bridge.council.termination_calls,
                         [(123,"testhost:claim-1",False),(456,"testhost:claim-2",False)])

    def test_stop_rejects_untrusted_running_worker_before_archive(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        task_id=next(iter(self.bridge.council.tasks)); task=self.bridge.council.tasks[task_id]
        cases=[(None,"testhost:claim",1,123,"testhost:claim"),
               (123,None,1,123,"testhost:claim"),
               (123,"testhost:claim",1,999,"testhost:claim")]
        for pid,claim_lock,run_id,run_pid,run_lock in cases:
            with self.subTest(pid=pid,claim_lock=claim_lock,run_pid=run_pid):
                task.status="running"; task.worker_pid=pid; task.claim_lock=claim_lock; task.current_run_id=run_id
                self.bridge.council.runs[task_id]=[type("Run",(),{"id":1,"status":"running",
                    "claim_lock":run_lock,"worker_pid":run_pid,"ended_at":None})()]
                with closing(sqlite3.connect(self.home/"fake-kanban.db")) as conn:
                    conn.execute("UPDATE tasks SET status='running',worker_pid=?,claim_lock=?,current_run_id=? WHERE id=?",
                                 (pid,claim_lock,run_id,task_id))
                    conn.execute("INSERT OR REPLACE INTO task_runs VALUES (1,?,'running',?,?,NULL)",
                                 (task_id,run_lock,run_pid)); conn.commit()
                before=len(self.bridge.council.archive_calls)
                status,value,_,_=self.action(workflow,"stop")
                self.assertEqual((status,value["error"]),(503,"worker_termination_unconfirmed"))
                self.assertEqual(len(self.bridge.council.archive_calls),before)
                state=self.bridge.load_state(workflow); state["phase"]="active"; self.bridge.save_state(state)

    def test_action_binding_duplicate_transfer_encoding_and_exact_graph(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        wrong=dict(self.action_body(workflow),nonce="f"*32)
        self.assertEqual(self.action(workflow,"stop",wrong)[0],409)
        db=self.home/"fake-kanban.db"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("INSERT INTO tasks VALUES ('unexpected-sixth','running',123,'testhost:claim',1)"); conn.commit()
        self.assertEqual(self.call("GET",f"/v1/councils/{workflow}")[0],409)
        self.assertEqual(self.action(workflow,"stop")[0],409)

        path="/v1/councils"; body=b""; headers=self.headers("POST",path,body)
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        conn.putrequest("POST",path,skip_host=True)
        for name,value in headers.items(): conn.putheader(name,value)
        conn.putheader("Transfer-Encoding","chunked"); conn.putheader("Transfer-Encoding","identity")
        conn.endheaders(); response=conn.getresponse()
        self.assertEqual(response.status,400); response.read(); conn.close()

    def test_absolute_read_deadline_cuts_trickle_client_and_serves_next_request(self):
        client=socket.create_connection(("127.0.0.1",self.server.server_port),timeout=2); client.settimeout(.05)
        request=("POST /v1/councils HTTP/1.1\r\nHost: "+bridge_module.DEFAULT_HOST_HEADER+
                 "\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{").encode()
        started=time.monotonic(); client.sendall(request); cut=False
        while time.monotonic()-started < 7:
            time.sleep(.1)
            try: client.sendall(b" ")
            except (BrokenPipeError,ConnectionResetError,OSError): cut=True; break
            try: response=client.recv(4096)
            except socket.timeout: continue
            except OSError: cut=True; break
            if response==b"": cut=True; break
        elapsed=time.monotonic()-started; client.close()
        self.assertTrue(cut); self.assertGreaterEqual(elapsed,4.5); self.assertLess(elapsed,6.5)
        self.assertEqual(self.call("GET","/healthz")[0],200)

    def test_upgrade_allows_archived_tombstone_but_rejects_active_release_mismatch(self):
        _,created,_,_=self.create(); archived_id=created["workflow_id"]
        self.action(archived_id,"stop"); self.action(archived_id,"archive")
        _,active,_,_=self.create(self.request_body("active-release",nonce="b"*32)); active_id=active["workflow_id"]
        self.runtime.write_text("pin=two\n")
        restarted=bridge_module.Bridge(home=self.home,install=self.install,state_root=self.state,
            workflow_root=self.workflow,key_file=self.key,contract_file=self.contract,
            runtime_contract=self.runtime,workflow_module=self.fake,min_free_bytes=1,now=lambda:self.now)
        self.assertEqual(restarted.load_state(archived_id)["phase"],"archived")
        with self.assertRaisesRegex(bridge_module.BridgeError,"state_identity_mismatch"):
            restarted.load_state(active_id)
        archived=self.bridge.state_path(archived_id); value=json.loads(archived.read_text())
        value["runtime_digest"]="invalid"; archived.write_text(bridge_module.canonical(value)+"\n"); archived.chmod(0o600)
        with self.assertRaisesRegex(bridge_module.BridgeError,"state_identity_mismatch"):
            restarted.load_state(archived_id)

    def test_runtime_rejects_missing_dispatch_lock_helper(self):
        self.fake.write_text(FAKE_COUNCIL.replace("def _dispatch_tick_lock(db_path):",
                                                  "def missing_dispatch_tick_lock(db_path):"))
        with self.assertRaisesRegex(bridge_module.BridgeError,"workflow_module_incompatible"):
            bridge_module.Bridge(home=self.home,install=self.install,state_root=self.state,
                workflow_root=self.workflow,key_file=self.key,contract_file=self.contract,
                runtime_contract=self.runtime,workflow_module=self.fake,min_free_bytes=1,now=lambda:self.now)

    def test_profile_generation_digest_uses_only_safe_immutable_definitions(self):
        profile=self.home/"profiles/council-reviewer-v2"
        baseline=bridge_module.framed_digest(profile,self.runtime)
        for relative in (".env","state.db","sessions/run.json","logs/run.log","cache/item","memory/item","runtime/item"):
            path=profile/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("mutable\n")
        self.assertEqual(bridge_module.framed_digest(profile,self.runtime),baseline)
        for relative in ("SOUL.md","config.yaml","skills/example/SKILL.md"):
            with self.subTest(relative=relative):
                path=profile/relative; original=path.read_bytes(); path.write_bytes(original+b"changed\n")
                self.assertNotEqual(bridge_module.framed_digest(profile,self.runtime),baseline)
                path.write_bytes(original)
        marker=profile/".council-profile"; marker.unlink(); marker.symlink_to(profile/"SOUL.md")
        with self.assertRaisesRegex(bridge_module.BridgeError,"profile_definition_unsafe"):
            bridge_module.framed_digest(profile,self.runtime)

    def test_state_board_mismatch_sqlite_guards_disk_and_reconcile_are_report_only(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        state=self.bridge.load_state(workflow); state["task_ids"]["review"]="wrong"; self.bridge.save_state(state)
        self.assertEqual(self.call("GET",f"/v1/councils/{workflow}")[0],409)
        state["task_ids"]["review"]="task-review"; self.bridge.save_state(state)
        db=self.home/"fake-kanban.db"
        with closing(__import__("sqlite3").connect(db)) as conn: conn.execute("PRAGMA journal_mode=WAL")
        self.assertEqual(self.call("GET",f"/v1/councils/{workflow}")[0],503)
        with closing(__import__("sqlite3").connect(db)) as conn: conn.execute("PRAGMA journal_mode=DELETE")
        db.write_bytes(b"not a sqlite database")
        self.assertEqual(self.call("GET",f"/v1/councils/{workflow}")[0],503)
        db.unlink(); self.bridge.council.board=False
        self.bridge.council.setup_v2(self.home,self.install,self.request_body(),self.bridge.contract)
        def snapshot():
            roots=(self.state,self.workflow,self.home/"fake-kanban.db")
            paths=[path for root in roots for path in ([root] if root.is_file() else root.rglob("*")) if path.is_file()]
            return {str(path):(path.stat().st_mtime_ns,hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths}
        before=snapshot()
        forbidden=AssertionError("read-only reconcile called pinned connector")
        with mock.patch.object(self.bridge.council.KB,"board_exists",side_effect=forbidden), \
                mock.patch.object(self.bridge.council.KBC,"connect",side_effect=forbidden), \
                mock.patch.object(self.bridge.council.KBC,"connect_closing",side_effect=forbidden), \
                mock.patch.object(self.bridge.council.KBC,"_dispatch_tick_lock",side_effect=forbidden), \
                mock.patch.object(self.bridge.council.KBC,"_resolve_busy_timeout_ms",side_effect=forbidden), \
                mock.patch.object(bridge_module,"load_module",return_value=self.bridge.council):
            report=self.read_only_bridge().reconcile_report()
        after=snapshot()
        self.assertEqual(before,after); self.assertFalse(report["workflows"][0]["mismatch"])
        with mock.patch.object(bridge_module.shutil,"disk_usage",return_value=type("D",(),{"free":0})()):
            with self.assertRaisesRegex(bridge_module.BridgeError,"insufficient_disk_headroom"):
                self.bridge.check_disk()

    def test_sqlite_requires_exact_resolved_and_connection_busy_timeout(self):
        _,created,_,_=self.create(); workflow=created["workflow_id"]
        self.assertEqual(bridge_module.KANBAN_BUSY_TIMEOUT_MS,120000)
        self.assertEqual(self.bridge.council.KBC._resolve_busy_timeout_ms(),120000)
        self.bridge.sqlite_check()
        self.bridge.council.resolved_busy_timeout=5000
        with self.assertRaisesRegex(bridge_module.BridgeError,"kanban_busy_timeout_mismatch"):
            self.bridge.sqlite_check()
        self.bridge.council.resolved_busy_timeout=120000; self.bridge.council.connector_busy_timeout=5000
        with self.assertRaisesRegex(bridge_module.BridgeError,"kanban_busy_timeout_mismatch"):
            self.bridge.sqlite_check()
        self.bridge.council.connector_busy_timeout=120000
        db=self.home/"fake-kanban.db"; lock=sqlite3.connect(db,check_same_thread=False)
        lock.execute("BEGIN EXCLUSIVE"); timer=threading.Timer(.1,lock.rollback); timer.start()
        try: self.bridge.sqlite_check()
        finally: timer.join(); lock.close()
        with mock.patch.object(self.bridge,"sqlite_check",wraps=self.bridge.sqlite_check) as check:
            self.call("GET",f"/v1/councils/{workflow}")
        self.assertEqual(check.call_args.kwargs,{})

    def test_logs_are_normalized_and_exclude_request_material(self):
        capture=io.StringIO()
        request=self.request_body("secret-operation"); request["snapshot_path"]="/secret/source?token=value"
        with mock.patch.object(sys,"stderr",capture): self.create(request)
        output=capture.getvalue(); record=json.loads(output.strip())
        for secret in ("secret-operation","owner/repo","/secret/source","token=value","source","result","signature","key"):
            self.assertNotIn(secret,output.lower())
        self.assertEqual(set(record),{"auth_generation","error","route_class","status","workflow_id"})
        self.assertEqual((record["route_class"],record["status"],record["auth_generation"]),("create",201,1))


class BridgePreflightTest(unittest.TestCase):
    def test_preflight_checks_rendered_install_and_is_inert(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); uid=os.getuid(); user=pwd.getpwuid(uid).pw_name
            home=root/"service/.hermes"; install=root/"install"; state=root/"state"; workflow=home/"workflow-runs"
            snapshot=root/"snapshots"; policy=root/"policy.md"
            for path in (home,install,state,state/"workflows",workflow): path.mkdir(parents=True,exist_ok=True); path.chmod(0o700)
            python=install/"venv/bin/python"; python.parent.mkdir(parents=True); python.symlink_to(sys.executable)
            package=install/"hermes_cli"; package.mkdir(); (package/"__init__.py").write_text("")
            connector=package/"kanban_db_connect.py"
            connector.write_text("""import sqlite3
def connect(path):
    connection=sqlite3.connect(path)
    connection.execute(\"PRAGMA journal_mode=DELETE\")
    connection.execute(\"PRAGMA busy_timeout=120000\")
    return connection
""")
            snapshot.mkdir(); snapshot.chmod(0o750); policy.write_text("policy\n"); policy.chmod(0o444)
            policy_digest=hashlib.sha256(policy.read_bytes()).hexdigest()
            files={}
            for name,mode in (("bridge",0o555),("reconcile",0o555),("risk",0o555),("preflight",0o555),
                              ("contract",0o444),("runtime",0o444)):
                path=root/name; path.write_text("fixed\n"); path.chmod(mode); files[name]=path
            files["contract"].chmod(0o644)
            shutil.copy(ROOT/"agent-config/hermes/workflows/pr-risk-council-kanban-v2.json",files["contract"])
            files["contract"].chmod(0o444)
            key_parent=root/"hermes-bridge-secrets"; key_parent.mkdir(); key_parent.chmod(0o750)
            key=key_parent/"key.json"; key.write_text(json.dumps({"schema_version":1,"auth_generation":1,"key":"ab"*32})); key.chmod(0o600)
            source=root/"source"; source.write_text("fixed\n")
            plist=root/"bridge.plist"
            env={"HOME":str(home.parent),"HERMES_HOME":str(home),"HERMES_INSTALL_DIR":str(install),
                 "HERMES_KANBAN_BRIDGE_BIND":"127.0.0.1","HERMES_KANBAN_BUSY_TIMEOUT_MS":"120000",
                 "HERMES_KANBAN_BRIDGE_PORT":"8766",
                 "HERMES_KANBAN_BRIDGE_HOST_HEADER":bridge_module.DEFAULT_HOST_HEADER,
                 "HERMES_KANBAN_BRIDGE_STATE_ROOT":str(state),"HERMES_KANBAN_BRIDGE_KEY_FILE":str(key),
                 "HERMES_KANBAN_BRIDGE_CONTRACT":str(files["contract"]),"HERMES_KANBAN_RISK_COUNCIL":str(files["risk"]),
                 "HERMES_NATIVE_CONTRACT":str(files["runtime"]),"PR_SAFETY_WORKFLOW_ROOT":str(workflow),
                 "PR_SAFETY_SNAPSHOT_ROOT":str(snapshot),"PR_SAFETY_POLICY_PATH":str(policy),
                 "PR_SAFETY_POLICY_VERSION":"v1","PR_SAFETY_POLICY_DIGEST":policy_digest}
            plist.write_bytes(plistlib.dumps({"Label":"test","UserName":user,
                "ProgramArguments":[str(install/"venv/bin/python"),str(files["bridge"])],
                "EnvironmentVariables":env,"RunAtLoad":False,"KeepAlive":{"SuccessfulExit":False}})); plist.chmod(0o644)
            tracked=[*files.values(),key,plist,policy]
            before={path:(path.stat().st_mode,path.stat().st_mtime_ns,path.read_bytes()) for path in tracked}
            command=[sys.executable,str(ROOT/"scripts/hermes-kanban-safety-bridge-preflight.py"),
                "--service-user",user,"--bridge",str(files["bridge"]),"--reconcile",str(files["reconcile"]),
                "--risk-council",str(files["risk"]),"--contract",str(files["contract"]),
                "--runtime-contract",str(files["runtime"]),"--self-path",str(files["preflight"]),
                "--plist",str(plist),"--key-file",str(key),"--state-root",str(state),
                "--workflow-root",str(workflow),"--hermes-home",str(home),"--install-dir",str(install),
                "--snapshot-root",str(snapshot),"--policy-path",str(policy),"--policy-version","v1",
                "--policy-digest",policy_digest,"--root-uid",str(uid),"--staff-gid",str(os.getgid()),
                "--installed-source",f"{files['bridge']}={source}"]
            result=subprocess.run(command,capture_output=True,text=True,check=True)
            report=json.loads(result.stdout)
            self.assertEqual(report["status"],"ready")
            self.assertEqual(report["sqlite"]["journal_mode"],"delete")
            self.assertEqual(report["sqlite"]["busy_timeout"],120000)
            self.assertEqual(report["sqlite"]["sqlite_version"],sqlite3.sqlite_version)
            after={path:(path.stat().st_mode,path.stat().st_mtime_ns,path.read_bytes()) for path in tracked}
            self.assertEqual(before,after)
            bad_digest=command.copy(); bad_digest[bad_digest.index(policy_digest)]="0"*64
            self.assertNotEqual(subprocess.run(bad_digest,capture_output=True).returncode,0)
            plist_data=plistlib.loads(plist.read_bytes())
            plist_data["EnvironmentVariables"]["HERMES_KANBAN_BUSY_TIMEOUT_MS"]="5000"
            plist.write_bytes(plistlib.dumps(plist_data)); plist.chmod(0o644)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
            plist_data["EnvironmentVariables"]["HERMES_KANBAN_BUSY_TIMEOUT_MS"]="120000"
            plist_data["EnvironmentVariables"]["EXTRA"]="forbidden"
            plist.write_bytes(plistlib.dumps(plist_data)); plist.chmod(0o644)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
            plist_data["EnvironmentVariables"].pop("EXTRA"); plist.write_bytes(plistlib.dumps(plist_data)); plist.chmod(0o644)
            snapshot.chmod(0o770)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
            snapshot.chmod(0o750)
            key_parent.chmod(0o755)
            parent_drift=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(parent_drift.returncode,0)
            self.assertIn("unsafe ownership or mode",parent_drift.stderr)
            key_parent.chmod(0o750)
            connector.write_text(connector.read_text().replace("busy_timeout=120000","busy_timeout=5000"))
            drift=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(drift.returncode,0)
            self.assertIn("pinned SQLite runtime settings changed",drift.stderr)


if __name__ == "__main__": unittest.main()
