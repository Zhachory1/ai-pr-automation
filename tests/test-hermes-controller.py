#!/usr/bin/env python3
import base64
import hashlib
import hmac
import importlib.util
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.modules.setdefault("psycopg", types.SimpleNamespace(connect=None))
sys.modules.setdefault("psycopg.rows", types.SimpleNamespace(dict_row=None))
spec = importlib.util.spec_from_file_location("hermes_controller", ROOT / "scripts/hermes-controller.py")
controller = importlib.util.module_from_spec(spec); spec.loader.exec_module(controller)
import hermes_pr_safety_result as safety_result


class LostSubmitHermes(BaseHTTPRequestHandler):
    bodies = []
    keys = {}
    lost = True

    def log_message(self, *_): pass

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        key = self.headers.get("Idempotency-Key")
        self.bodies.append(body)
        if key not in self.keys:
            self.keys[key] = ("run-stable", body)
        if self.lost:
            self.__class__.lost = False
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        run_id, first = self.keys[key]
        if first != body:
            self.send_response(409); self.end_headers(); return
        raw = json.dumps({"run_id": run_id, "status": "started", "replayed": True}).encode()
        self.send_response(202); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw)


class SignedBridge(BaseHTTPRequestHandler):
    key = bytes.fromhex("ab" * 32)
    responses = []
    requests = []

    def log_message(self, *_): pass

    def _handle(self):
        length = int(self.headers.get("content-length", "0")); body = self.rfile.read(length)
        self.__class__.requests.append((self.command,self.path,body,self.headers.get("Host"),
                                        self.headers.get("X-Hermes-Nonce")))
        status,value,forged = self.__class__.responses.pop(0)
        raw = (controller.canonical(value) + "\n").encode(); timestamp = int(__import__("time").time())
        nonce = self.headers["X-Hermes-Nonce"]; digest = hashlib.sha256(raw).hexdigest()
        signature = hmac.new(self.key, controller.BridgeClient.preimage(
            1,timestamp,nonce,digest,self.command,self.path,status), hashlib.sha256).hexdigest()
        self.send_response(status); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw))); self.send_header("X-Hermes-Auth-Generation","1")
        self.send_header("X-Hermes-Timestamp",str(timestamp)); self.send_header("X-Hermes-Nonce",nonce)
        self.send_header("X-Hermes-Body-SHA256",digest)
        self.send_header("X-Hermes-Signature","0" * 64 if forged else signature)
        self.end_headers(); self.wfile.write(raw)

    do_GET = _handle
    do_POST = _handle


class ControllerContractTest(unittest.TestCase):
    def council_package(self, verdict="clear", attempt=None):
        workflow = attempt["run_id"].removeprefix("kanban:") if attempt else "workflow"
        artifact = self.artifact_digest(attempt) if attempt else "a" * 64
        return {"workflow_id":workflow,"artifact_digest":artifact,"verdict":verdict,"intent":{},
                "findings":[],"coverage":{},"documentation":{},"observability":{},
                "incident":{"candidate":False,"changed_line_cause":False,"concrete_trigger":False,
                            "severe_impact":False,"high_confidence_chain":False,
                            "stop_rollback_or_page":False,"evidence":[]},
                "human_decisions_needed":[],"dissent":[],"residual_risk":[]}

    def safety_payload(self):
        return {"operation_id":"op","repo":"o/r","pr":1,"head_sha":"h","base_sha":"b",
                "diff_hash":"d","policy_version":"v1","policy_digest":"p"}

    def kanban_attempt(self, request_status="running"):
        payload = dict(self.safety_payload(), snapshot_path="/snapshot", policy_path="/policy")
        nonce = "a" * 32
        body = controller.canonical(payload | {"nonce":nonce}).encode()
        workflow = "pr-risk-council-" + hashlib.sha256(f"op:{nonce}".encode()).hexdigest()[:32]
        return {"request_id":1,"attempt_no":1,"kind":"pr-safety-review","nonce":nonce,
                "payload":payload,"dedupe_key":"safety","profile":"pr-safety-v1","route_generation":1,
                "request_b64":base64.b64encode(body).decode(),"request_digest":hashlib.sha256(body).hexdigest(),
                "run_id":"kanban:" + workflow,"request_status":request_status,
                "lease_expired":False,"stop_requested":False,"stop_confirmed":False,
                "terminal_status":None,"output_digest":None,"output_b64":None}

    def artifact_digest(self, attempt, contract_digest="c" * 64):
        request = json.loads(base64.b64decode(attempt["request_b64"]))
        return hashlib.sha256(controller.canonical(
            {"request":request,"contract_digest":contract_digest}).encode()).hexdigest()

    def bridge_state(self, attempt, phase="terminal", package=None, failure=None):
        workflow = attempt["run_id"].removeprefix("kanban:")
        digest = hashlib.sha256(controller.canonical(package).encode()).hexdigest() if package is not None else None
        return {"schema_version":1,"phase":phase,"workflow_id":workflow,"operation_id":"op",
                "request_body_digest":attempt["request_digest"],"safety_request_nonce":attempt["nonce"],
                "artifact_digest":self.artifact_digest(attempt),"contract_digest":"c"*64,
                "profile_generations":{"council":"d"*64},"runtime_digest":"e"*64,
                "task_ids":{role:"task-"+role for role in
                    ("review","security","reliability","architecture","synthesis")},
                "created_at":1,"deadline_at":4102444800,"result_digest":digest,
                "result_package":package,"terminal_failure":failure,
                "archive_cleanup_confirmed":phase == "archived"}

    def test_run_status_validation_is_status_specific(self):
        running = {"object":"hermes.run","run_id":"r","status":"running","created_at":1.0,
                   "updated_at":2.0,"last_event":"run.started","session_id":"s","model":"m"}
        self.assertTrue(controller.valid_run_status(running, "r"))
        sparse = {"object":"hermes.run","run_id":"r","status":"queued","created_at":1.0,"updated_at":1.0}
        self.assertTrue(controller.valid_run_status(sparse, "r"))
        self.assertFalse(controller.valid_run_status(dict(running, status="completed"), "r", terminal=True))
        self.assertFalse(controller.valid_run_status(dict(running, status="completed", output="{}"), "r", terminal=True))
        self.assertTrue(controller.valid_run_status(dict(running, status="completed", output="{}", usage={}), "r", terminal=True))
        self.assertTrue(controller.valid_run_status(dict(running, status="failed", error="agent failed"), "r", terminal=True))
        self.assertFalse(controller.valid_run_status(dict(running, status="failed"), "r", terminal=True))

    def test_typed_output_is_strict_and_safety_allows_one_embedded_object(self):
        self.assertEqual(controller.parse_typed_output('{"x":1}'), {"x":1})
        self.assertEqual(controller.parse_typed_output('```json\n{"x":1}\n```'), {"x":1})
        self.assertIsNone(controller.parse_typed_output('analysis first\n```json\n{"x":1}\n```'))
        self.assertIsNone(controller.parse_typed_output('analysis first\n{"x":{"y":1}}'))
        clear = '{"nonce":"n","operation_id":"o","status":"clear","incident":{"candidate":false}}'
        incident = '{"nonce":"n","operation_id":"o","status":"incident_candidate","incident":{"candidate":true}}'
        self.assertEqual(controller.parse_safety_output(f'analysis {{}} first\n```json\n{clear}\n```')["status"], "clear")
        self.assertEqual(controller.parse_safety_output(f'analysis first\n{clear}')["status"], "clear")
        self.assertIsNone(controller.parse_safety_output(f'{incident}\n```json\n{clear}\n```'))
        self.assertIsNone(controller.parse_safety_output('prose {"x":1} then {"x":2}'))

    def test_periodic_recovery_reschedules_open_attempts(self):
        attempt = {"request_id":7,"attempt_no":1}
        class DB:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def execute(self, query): self.query = query; return self
            def fetchall(self): return [{"hermes_api_open_attempts":attempt}]
        instance = controller.Controller.__new__(controller.Controller)
        db = DB(); calls = []
        instance.connect = lambda: db
        instance.schedule = lambda pool, value, recovering=False: calls.append((pool,value,recovering))
        pool = object()
        self.assertEqual(instance.recover_open(pool), 1)
        self.assertEqual(db.query, "SELECT * FROM hermes_api_open_attempts()")
        self.assertEqual(calls, [(pool,attempt,True)])

    def test_poll_method_is_not_shadowed_by_interval(self):
        instance = controller.Controller.__new__(controller.Controller)
        instance.poll_interval = 2.0
        self.assertTrue(callable(instance.poll))

    def test_lost_submit_replays_identical_bytes_and_key(self):
        LostSubmitHermes.bodies = []; LostSubmitHermes.keys = {}; LostSubmitHermes.lost = True
        server = ThreadingHTTPServer(("127.0.0.1", 0), LostSubmitHermes)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            client = controller.HermesClient(f"http://127.0.0.1:{server.server_port}", {"p": "key"})
            body = b'{"input":"exact","session_id":"s","instructions":"strict"}'
            with self.assertRaises(OSError): client.request("POST", "p", "", body, "stable-key")
            status, response = client.request("POST", "p", "", body, "stable-key")
            self.assertEqual((status, response["run_id"]), (202, "run-stable"))
            self.assertEqual(LostSubmitHermes.bodies, [body, body])
            self.assertEqual(set(LostSubmitHermes.keys), {"stable-key"})
        finally:
            server.shutdown(); server.server_close()

    def test_direct_effect_output_is_exact_and_head_bound(self):
        nonce = "a" * 32; head = "b" * 40
        value = {"detail":"ok","nonce":nonce,"posted_ref":f"<!-- ai-pr-automation head={head} -->","status":"done"}
        embedded = "analysis {} first\n" + json.dumps(value)
        self.assertEqual(controller.parse_direct_output(embedded), value)
        self.assertIsNone(controller.parse_direct_output(embedded + "\n" + json.dumps(dict(value, status="skipped"))))
        self.assertEqual(controller.valid_generic("pr-review", value, nonce, {}, f"o/r#1@{head}"), value)
        self.assertIsNone(controller.valid_generic("pr-review", dict(value, extra=True), nonce, {}, f"o/r#1@{head}"))
        self.assertIsNone(controller.valid_generic("pr-review", dict(value, posted_ref="wrong"), nonce, {}, f"o/r#1@{head}"))

    def test_failed_outcome_settlement_depends_on_kind(self):
        nonce = "a" * 32; head = "b" * 40

        def process(kind, terminal):
            instance = controller.Controller.__new__(controller.Controller)
            instance.lock = threading.Lock(); instance.running = set()
            instance.submit = lambda attempt: "run"
            instance.poll = lambda attempt, run_id: terminal
            settlements = []
            instance.settle = lambda attempt, status, detail, posted="", attempt_state=None: settlements.append(
                (status, attempt_state)) or True
            attempt = {"request_id":1,"attempt_no":1,"kind":kind,"nonce":nonce,"payload":{},
                       "dedupe_key":f"o/r#1@{head}"}
            instance.process(attempt)
            return settlements

        reconcile = json.dumps({"detail":"uncertain","nonce":nonce,"posted_ref":"","status":"reconcile"})
        for terminal in (("failed", ""), ("completed", "not json"), ("completed", reconcile)):
            with self.subTest(kind="pr-review", terminal=terminal[0], output=terminal[1]):
                self.assertEqual(process("pr-review", terminal), [("failed", "failed")])
        for kind, expected in (("pr-maintain", "reconcile"), ("swe-implement", "reconcile"),
                               ("doc-write", "failed")):
            with self.subTest(kind=kind):
                self.assertEqual(process(kind, ("failed", "")), [(expected, expected)])

    def test_shared_safety_result_contract_is_controller_contract(self):
        self.assertIs(controller.map_council_safety, safety_result.map_council_safety)
        self.assertIs(controller.normalize_safety, safety_result.normalize_safety)
        self.assertIs(controller.valid_safety, safety_result.valid_safety)

    def test_safety_clear_is_incident_free_and_identity_bound(self):
        payload = {"operation_id":"op","repo":"o/r","pr":1,"head_sha":"h","base_sha":"b",
                   "diff_hash":"d","policy_version":"v1","policy_digest":"p"}
        nonce = "a" * 32
        value = dict(payload, nonce=nonce, status="clear", intent={}, findings=[], coverage={}, documentation={},
                     observability={}, incident={"candidate":False}, human_decisions_needed=[])
        self.assertTrue(controller.valid_safety(value, payload, nonce))
        self.assertFalse(controller.valid_safety(dict(value, findings=[{"claim":"x"}]), payload, nonce))
        self.assertFalse(controller.valid_safety(dict(value, incident={"candidate":True}), payload, nonce))
        self.assertFalse(controller.valid_safety(dict(value, nonce="b" * 32), payload, nonce))
        normalized = controller.normalize_safety(dict(value, status="needs_human_decision",
            incident={"candidate":True}, policy_path="/policy", snapshot_path="/snapshot"))
        self.assertEqual(normalized["status"], "incident_candidate")
        self.assertNotIn("policy_path", normalized); self.assertNotIn("snapshot_path", normalized)

        calls = []
        instance = controller.Controller.__new__(controller.Controller)
        instance.db_bool = lambda query, params: calls.append((query, params)) or True
        attempt = {"request_id":1,"attempt_no":1,"nonce":nonce,"payload":payload}
        instance.postprocess_safety(attempt, value)
        settlement = calls[0][1]
        self.assertEqual(settlement[2:7], ("done", "clear", False, None, None))

        incident = dict(value, status="needs_human_decision", incident={"candidate":True},
                        findings=[{"severity":"high"}])
        attempt.update(run_id="run", profile="pr-safety-v1")
        with tempfile.TemporaryDirectory() as handoffs, mock.patch.dict("os.environ", {"HANDOFF_ROOT":handoffs}):
            calls.clear(); instance.postprocess_safety(attempt, incident)
            settlement = calls[0][1]
            self.assertTrue(settlement[4])
            self.assertEqual(json.loads(settlement[5])["status"], "incident_candidate")
            instance.postprocess_safety(attempt, incident)
            self.assertEqual(len(list(pathlib.Path(handoffs).iterdir())), 1)

    def test_council_mapping_clean_findings_inconclusive_and_forged_identity(self):
        payload=self.safety_payload(); nonce="a"*32
        clean=controller.map_council_safety(self.council_package(),payload,nonce)
        self.assertEqual(clean["status"],"clear")
        self.assertTrue(controller.valid_safety(clean,payload,nonce))
        self.assertEqual(clean["coverage"]["council"]["workflow_id"],"workflow")

        package=self.council_package("changes_requested")
        package["operation_id"]="forged"; package["repo"]="evil/repo"
        package["findings"]=[{"role":"security","claim":"breakage","evidence":[],"confidence":"high",
                              "dissent":[],"residual_risk":[]}]
        dissent={"source_role":"security","claim":"decide","evidence":[],
                 "disposition":"unresolved","rationale":"uncertain"}
        risk={"source_role":"reliability","claim":"bounded risk","evidence":[],
              "requires_human_decision":True}
        package["dissent"]=[dissent]; package["residual_risk"]=[risk]
        findings=controller.map_council_safety(package,payload,nonce)
        self.assertEqual(findings["status"],"changes_requested")
        self.assertEqual(findings["operation_id"],"op"); self.assertEqual(findings["repo"],"o/r")
        self.assertIn(dissent,findings["human_decisions_needed"]); self.assertIn(risk,findings["human_decisions_needed"])
        self.assertTrue(controller.valid_safety(findings,payload,nonce))

        inconclusive=controller.map_council_safety(self.council_package("inconclusive"),payload,nonce)
        self.assertEqual(inconclusive["status"],"needs_human_decision")
        self.assertTrue(controller.valid_safety(inconclusive,payload,nonce))

    def test_council_mapping_requires_all_five_incident_predicates_and_evidence(self):
        payload=self.safety_payload(); nonce="a"*32; package=self.council_package("incident_candidate")
        package["incident"]["candidate"]=True
        package["incident"]["evidence"]=[{"path":"app.py","line":7,"side":"new","quote":"danger()"}]
        for key in ("changed_line_cause","concrete_trigger","severe_impact","high_confidence_chain",
                    "stop_rollback_or_page"):
            package["incident"][key]=True
        mapped=controller.map_council_safety(package,payload,nonce)
        self.assertEqual(mapped["status"],"incident_candidate"); self.assertTrue(mapped["incident"]["candidate"])
        self.assertTrue(controller.valid_safety(mapped,payload,nonce))
        package["incident"]["severe_impact"]=False
        with self.assertRaisesRegex(ValueError,"inconsistent"):
            controller.map_council_safety(package,payload,nonce)

        for evidence in ([], [{"path":"app.py","line":7,"side":"new","quote":""}],
                         [{"path":"forged"}]):
            forged=self.council_package("incident_candidate"); forged["incident"]["candidate"]=True
            for key in ("changed_line_cause","concrete_trigger","severe_impact","high_confidence_chain",
                        "stop_rollback_or_page"):
                forged["incident"][key]=True
            forged["incident"]["evidence"]=evidence
            with self.assertRaisesRegex(ValueError,"requires evidence"):
                controller.map_council_safety(forged,payload,nonce)
        predicate=self.council_package("changes_requested"); predicate["incident"]["severe_impact"]=True
        with self.assertRaisesRegex(ValueError,"requires evidence"):
            controller.map_council_safety(predicate,payload,nonce)
        inconsistent=self.council_package("incident_candidate")
        with self.assertRaisesRegex(ValueError,"inconsistent"):
            controller.map_council_safety(inconsistent,payload,nonce)

    def test_safety_handoff_renders_optional_council_context(self):
        payload=self.safety_payload(); nonce="a"*32
        value=controller.map_council_safety(self.council_package("needs_human_decision"),payload,nonce)
        instance=controller.Controller.__new__(controller.Controller)
        with tempfile.TemporaryDirectory() as handoffs, mock.patch.dict("os.environ",{"HANDOFF_ROOT":handoffs}):
            path,_=instance.write_safety_handoff({"payload":payload,"nonce":nonce},value)
            text=pathlib.Path(path).read_text()
        self.assertIn("## Concrete breakage",text); self.assertIn("## Human decisions",text)
        self.assertIn("## Council context",text); self.assertIn('"workflow_id":"workflow"',text)

    def test_bridge_client_verifies_signed_response_host_and_forgery(self):
        SignedBridge.responses = [(200,{"status":"ok","auth_generation":1},False),
                                  (200,{"status":"ok","auth_generation":1},False),
                                  (200,{"status":"ok","auth_generation":1},True)]
        SignedBridge.requests = []
        server = ThreadingHTTPServer(("127.0.0.1",0), SignedBridge)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                key = pathlib.Path(root) / "key.json"
                key.write_text(json.dumps({"schema_version":1,"auth_generation":1,"key":"ab"*32}))
                client = controller.BridgeClient(f"http://127.0.0.1:{server.server_port}",key,
                                                 "hermes-council.localhost:8766")
                self.assertEqual(client.request("GET","/healthz")[0],200)
                self.assertEqual(client.request("GET","/healthz")[0],200)
                with self.assertRaisesRegex(ValueError,"authentication"):
                    client.request("GET","/healthz")
            self.assertEqual({row[3] for row in SignedBridge.requests},{"hermes-council.localhost:8766"})
            self.assertEqual(len({row[4] for row in SignedBridge.requests}),3)
        finally:
            server.shutdown(); server.server_close()

    def test_single_engine_uses_runs_api_for_new_claim_but_bridge_for_recovered_kanban_marker(self):
        raw = {"id":1,"kind":"pr-safety-review","payload":self.safety_payload(),"dedupe_key":"safety",
               "route_generation":1,"auth_generation":1,"profile_generation":"f"*64}
        class DB:
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def execute(self,query,params): self.query,self.params=query,params; return self
            def fetchone(self): return {"attempt":{"request_id":1}}
        bridge_calls = []
        class Bridge:
            response = None
            def request(self,method,path,body=None):
                bridge_calls.append((method,path,body)); return 200,self.response
        instance = controller.Controller.__new__(controller.Controller)
        instance.safety_engine = "single"; instance.auth_generation = 1
        instance.generations = {"pr-safety-review":"f"*64}; instance.lease = 120
        instance.candidate = lambda kind: dict(raw)
        instance.build_prompt = lambda candidate: ("exact existing prompt",None)
        instance._bridge = Bridge(); db = DB(); instance.connect = lambda: db
        self.assertEqual(instance.claim("pr-safety-review")["request_id"],1)
        self.assertEqual(bridge_calls,[])
        self.assertIn("hermes_claim_request",db.query)
        self.assertNotIn("kanban",db.query.lower())
        self.assertEqual(db.params[2],"exact existing prompt")

        attempt = self.kanban_attempt(); instance._bridge.response = self.bridge_state(attempt,phase="active")
        instance.lock = threading.Lock(); instance.running = {(1,1)}; recovery_calls = []
        instance.db_bool = lambda query,params: recovery_calls.append("recover") or True
        instance.safety_preflight = lambda payload: recovery_calls.append("preflight")
        instance.poll_kanban = lambda value,state: recovery_calls.append("poll")
        instance.process(attempt,recovering=True)
        self.assertEqual(recovery_calls,["recover","preflight","poll"])
        self.assertEqual(bridge_calls,[("POST","/v1/councils",base64.b64decode(attempt["request_b64"]))])

    def test_kanban_request_bytes_accepts_postgres_wrapped_base64(self):
        attempt=self.kanban_attempt(); encoded=attempt["request_b64"]
        attempt["request_b64"]="\n".join(encoded[index:index+76] for index in range(0,len(encoded),76))
        instance=controller.Controller.__new__(controller.Controller)
        self.assertEqual(instance.kanban_request_bytes(attempt),base64.b64decode(encoded))
        attempt["request_b64"] += "!"
        with self.assertRaises(ValueError): instance.kanban_request_bytes(attempt)

    def test_kanban_claim_workflow_changes_only_with_safety_nonce(self):
        raw = {"id":1,"kind":"pr-safety-review","payload":dict(
            self.safety_payload(),snapshot_path="/snapshot",policy_path="/policy"),
            "dedupe_key":"safety","route_generation":1,"auth_generation":1,
            "profile_generation":"f"*64}
        calls = []
        class DB:
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def execute(self,query,params): calls.append(params); return self
            def fetchone(self): return {"attempt":None}
        instance = controller.Controller.__new__(controller.Controller)
        instance.safety_engine = "kanban"; instance.lease = 120
        instance.candidate = lambda kind: dict(raw)
        instance.safety_preflight = lambda payload: None
        instance.connect = DB
        with mock.patch.object(controller.os,"urandom",side_effect=[b"a"*16,b"b"*16]):
            instance.claim("pr-safety-review"); instance.claim("pr-safety-review")
        for params in calls:
            nonce,workflow = params[1],params[6]
            self.assertEqual(workflow,"pr-risk-council-" + hashlib.sha256(f"op:{nonce}".encode()).hexdigest()[:32])
        self.assertNotEqual(calls[0][6],calls[1][6])

    def test_kanban_create_lost_response_replays_exact_body(self):
        attempt = self.kanban_attempt(); state = self.bridge_state(attempt,phase="active"); seen = []
        class Bridge:
            def request(self,method,path,body=None):
                seen.append(body)
                if len(seen) == 1: raise OSError("lost response")
                return 200,state
        instance = controller.Controller.__new__(controller.Controller); instance._bridge = Bridge()
        self.assertEqual(instance.create_kanban(attempt),state)
        expected = base64.b64decode(attempt["request_b64"])
        self.assertEqual(seen,[expected,expected])

    def test_kanban_create_conflict_busy_and_timeout_fail_without_blind_archive(self):
        for outcome in (409,429,"timeout"):
            with self.subTest(outcome=outcome):
                attempt = self.kanban_attempt(); calls = []
                class Bridge:
                    count = 0
                    def request(self,method,path,body=None):
                        self.count += 1
                        if path == "/v1/councils":
                            if outcome == "timeout": raise OSError("timeout")
                            return outcome,{"error":"workflow_busy" if outcome == 429 else "request_identity_conflict"}
                        return 404,{"error":"unknown_workflow"}
                instance = controller.Controller.__new__(controller.Controller); instance._bridge = Bridge()
                instance.settle_safety_failure = lambda a,d: calls.append("settle")
                instance.complete_effect = lambda a,s,error=None: calls.append(("complete",s))
                instance.kanban_archive = lambda a: calls.append("archive")
                self.assertIsNone(instance.create_kanban(attempt))
                self.assertEqual(calls,["settle",("complete","failed")])

    def test_bridge_state_rejects_cross_artifact_and_package_identity(self):
        attempt = self.kanban_attempt(); package = self.council_package(attempt=attempt)
        instance = controller.Controller.__new__(controller.Controller)
        state = self.bridge_state(attempt,package=package)
        self.assertEqual(instance.validate_bridge_state(attempt,state),state)
        for key,value in (("workflow_id","pr-risk-council-"+"0"*32),("artifact_digest","0"*64)):
            with self.subTest(outer=key), self.assertRaisesRegex(ValueError,"malformed Kanban bridge state"):
                instance.validate_bridge_state(attempt,dict(state,**{key:value}))
        for key,value in (("workflow_id","pr-risk-council-"+"0"*32),("artifact_digest","0"*64)):
            forged_package = dict(package,**{key:value})
            forged = dict(state,result_package=forged_package,
                result_digest=hashlib.sha256(controller.canonical(forged_package).encode()).hexdigest())
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,"result package"):
                instance.validate_bridge_state(attempt,forged)

    def test_kanban_terminal_order_and_malformed_package_failure(self):
        attempt = self.kanban_attempt(); package = self.council_package(attempt=attempt); state = self.bridge_state(attempt,package=package)
        instance = controller.Controller.__new__(controller.Controller); calls = []
        instance.record_kanban_terminal = lambda a,s,v: calls.append(("record",s))
        instance.settle_safety_result = lambda a,v: calls.append(("settle",v["status"]))
        instance.kanban_archive = lambda a: calls.append("archive")
        instance.complete_effect = lambda a,s,error=None: calls.append(("complete",s))
        instance.settle_kanban_terminal(attempt,state)
        self.assertEqual(calls,[("record","completed"),("settle","clear"),"archive",("complete","completed")])

        malformed = dict(state,result_package={"workflow_id":"wrong"},
                         result_digest=hashlib.sha256(b'{"workflow_id":"wrong"}').hexdigest())
        failures = []
        instance.finish_kanban_failure = lambda a,d,state=None: failures.append((d,state))
        instance.settle_kanban_terminal(attempt,malformed)
        self.assertIn("malformed Kanban package",failures[0][0])

    def test_completed_terminal_record_recovery_renews_and_settles_without_stop(self):
        attempt = self.kanban_attempt(); attempt["lease_expired"] = True
        package = self.council_package(attempt=attempt); state = self.bridge_state(attempt,package=package)
        recorded = []
        crashing = controller.Controller.__new__(controller.Controller)
        crashing.record_kanban_terminal = lambda a,s,v: recorded.append((s,controller.canonical(v).encode()))
        crashing.settle_safety_result = lambda *_: (_ for _ in ()).throw(RuntimeError("crash after terminal record"))
        crashing.kanban_archive = lambda *_: (_ for _ in ()).throw(AssertionError("settlement did not crash"))
        with self.assertRaisesRegex(RuntimeError,"crash after terminal record"):
            crashing.settle_kanban_terminal(attempt,state)
        status,raw = recorded[0]
        attempt.update(terminal_status=status,output_digest=hashlib.sha256(raw).hexdigest(),
                       output_b64=base64.b64encode(raw).decode())
        instance = controller.Controller.__new__(controller.Controller)
        instance.lease = 120; instance.lock = threading.Lock(); instance.running = {(1,1)}; calls = []
        def db_bool(query,params):
            calls.append("recover" if "recover_lease" in query else "record")
            return True
        instance.db_bool = db_bool
        instance.kanban_status = lambda a: calls.append("status") or ("state",state)
        instance.settle_safety_result = lambda a,v: calls.append(("settle",v["status"]))
        instance.kanban_archive = lambda a: calls.append("archive")
        instance.complete_effect = lambda a,s,error=None: calls.append(("complete",s))
        instance.stop_and_fail_kanban = lambda *_: (_ for _ in ()).throw(AssertionError("terminal recovery stopped"))
        instance.safety_preflight = lambda *_: (_ for _ in ()).throw(AssertionError("terminal recovery preflighted"))
        instance.create_kanban = lambda *_: (_ for _ in ()).throw(AssertionError("terminal recovery recreated"))
        instance.process(attempt,recovering=True)
        self.assertEqual(calls,["recover","status","record",("settle","clear"),"archive",("complete","completed")])

    def test_failed_terminal_record_recovery_renews_and_settles_without_stop(self):
        attempt = self.kanban_attempt(); attempt["lease_expired"] = True
        state = self.bridge_state(attempt,failure="member_failed"); recorded = []
        crashing = controller.Controller.__new__(controller.Controller)
        crashing.record_kanban_terminal = lambda a,s,v: recorded.append((s,controller.canonical(v).encode()))
        crashing.settle_safety_failure = lambda *_: (_ for _ in ()).throw(RuntimeError("crash after terminal record"))
        crashing.kanban_archive = lambda *_: (_ for _ in ()).throw(AssertionError("settlement did not crash"))
        with self.assertRaisesRegex(RuntimeError,"crash after terminal record"):
            crashing.settle_kanban_terminal(attempt,state)
        status,raw = recorded[0]
        attempt.update(terminal_status=status,output_digest=hashlib.sha256(raw).hexdigest(),
                       output_b64=base64.b64encode(raw).decode())
        instance = controller.Controller.__new__(controller.Controller)
        instance.lease = 120; instance.lock = threading.Lock(); instance.running = {(1,1)}; calls = []
        def db_bool(query,params):
            calls.append("recover" if "recover_lease" in query else "record")
            return True
        instance.db_bool = db_bool
        instance.kanban_status = lambda a: calls.append("status") or ("state",state)
        instance.settle_safety_failure = lambda a,d: calls.append(("settle",d))
        instance.kanban_archive = lambda a: calls.append("archive")
        instance.complete_effect = lambda a,s,error=None: calls.append(("complete",s))
        instance.stop_and_fail_kanban = lambda *_: (_ for _ in ()).throw(AssertionError("terminal recovery stopped"))
        instance.safety_preflight = lambda *_: (_ for _ in ()).throw(AssertionError("terminal recovery preflighted"))
        instance.create_kanban = lambda *_: (_ for _ in ()).throw(AssertionError("terminal recovery recreated"))
        instance.process(attempt,recovering=True)
        self.assertEqual(calls,["recover","status","record",("settle","Kanban terminal failure: member_failed"),
                                "archive",("complete","failed")])

    def test_completed_terminal_record_cannot_be_overwritten_as_failed(self):
        attempt = self.kanban_attempt(); package = self.council_package(attempt=attempt)
        attempt.update(terminal_status="completed",
            output_digest=hashlib.sha256(controller.canonical(package).encode()).hexdigest())
        instance = controller.Controller.__new__(controller.Controller)
        instance.db_bool = lambda *_: (_ for _ in ()).throw(AssertionError("immutable terminal reached SQL"))
        with self.assertRaisesRegex(ValueError,"immutable Kanban terminal"):
            instance.record_kanban_terminal(attempt,"failed",self.bridge_state(attempt,failure="member_failed"))

    def test_expired_or_stop_pending_recovery_stops_before_lease_recovery(self):
        for flag in ("lease_expired","stop_requested"):
            with self.subTest(flag=flag):
                attempt = self.kanban_attempt(); attempt[flag] = True
                instance = controller.Controller.__new__(controller.Controller)
                instance.safety_engine = "single"; instance.lease = 120
                instance.lock = threading.Lock(); instance.running = {(1,1)}; calls = []
                instance.stop_and_fail_kanban = lambda a,d,recover: calls.append(("stop",recover)) or False
                instance.db_bool = lambda *_: (_ for _ in ()).throw(AssertionError("lease recovered before stop"))
                instance.process(attempt,recovering=True)
                self.assertEqual(calls,[("stop",True)])

    def test_expired_claim_without_bridge_state_confirms_no_effect_and_settles(self):
        attempt = self.kanban_attempt(); attempt["lease_expired"] = True
        instance = controller.Controller.__new__(controller.Controller)
        instance.lease = 120; instance.lock = threading.Lock(); instance.running = {(1,1)}; calls = []
        class Bridge:
            def request(self,method,path,body=None):
                calls.append((method,path)); return 404,{"error":"unknown_workflow"}
        instance._bridge = Bridge()
        def db_bool(query,params):
            calls.append("prepare" if "prepare_kanban_stop" in query else
                         "confirm" if "confirm_kanban_stop" in query else "recover")
            return True
        instance.db_bool = db_bool
        instance.settle_safety_failure = lambda a,d: calls.append("settle")
        instance.complete_effect = lambda a,s,error=None: calls.append(("complete",s))
        instance.kanban_archive = lambda *_: (_ for _ in ()).throw(AssertionError("missing workflow archived"))
        instance.process(attempt,recovering=True)
        self.assertEqual(calls,["prepare",("POST","/v1/councils/" +
            attempt["run_id"].removeprefix("kanban:") + "/stop"),"confirm","recover","settle",("complete","failed")])

    def test_board_mismatch_does_not_confirm_recover_or_settle(self):
        attempt = self.kanban_attempt(); attempt["lease_expired"] = True
        instance = controller.Controller.__new__(controller.Controller)
        instance.lease = 120; instance.lock = threading.Lock(); instance.running = {(1,1)}; calls = []
        class Bridge:
            def request(self,method,path,body=None): return 409,{"error":"state_board_mismatch"}
        instance._bridge = Bridge()
        instance.db_bool = lambda query,params: calls.append("prepare") or True
        instance.settle_safety_failure = lambda *_: calls.append("settle")
        instance.complete_effect = lambda *_: calls.append("complete")
        instance.process(attempt,recovering=True)
        self.assertEqual(calls,["prepare"])

    def test_deadline_stop_failure_returns_without_lease_recovery(self):
        attempt = self.kanban_attempt(); instance = controller.Controller.__new__(controller.Controller)
        instance.lease = 120; calls = []
        instance.stop_and_fail_kanban = lambda a,d,recover_lease=False: calls.append((d,recover_lease)) or False
        instance.poll_kanban(attempt,self.bridge_state(attempt,phase="active") | {"deadline_at":0})
        self.assertEqual(calls,[("Kanban deadline exceeded; stop confirmed",False)])

    def test_kanban_archive_crash_leaves_completion_for_recovery(self):
        attempt = self.kanban_attempt(); state = self.bridge_state(
            attempt,package=self.council_package(attempt=attempt))
        instance = controller.Controller.__new__(controller.Controller); calls = []
        instance.record_kanban_terminal = lambda a,s,v: calls.append("record")
        instance.settle_safety_result = lambda a,v: calls.append("settle")
        instance.kanban_archive = lambda a: (_ for _ in ()).throw(OSError("crash"))
        instance.complete_effect = lambda *args: calls.append("complete")
        with self.assertRaises(OSError): instance.settle_kanban_terminal(attempt,state)
        self.assertEqual(calls,["record","settle"])

    def test_lease_loss_requires_persisted_intent_and_signed_stop_before_recovery(self):
        attempt = self.kanban_attempt(); instance = controller.Controller.__new__(controller.Controller)
        instance.lease = 120; calls = []
        instance.kanban_stop = lambda a: (_ for _ in ()).throw(ValueError("forged"))
        instance.db_bool = lambda query,params: calls.append("prepare") or True
        self.assertFalse(instance.stop_and_fail_kanban(attempt,"lost",True))
        self.assertEqual(calls,["prepare"])
        calls.clear(); stopped = self.bridge_state(attempt,phase="stopped")
        instance.kanban_stop = lambda a: calls.append("stop") or stopped
        instance.db_bool = lambda query,params: calls.append(
            "prepare" if "prepare_kanban_stop" in query else "recover" if "recover_lease" in query else "mark") or True
        instance.finish_kanban_failure = lambda a,d,state=None: calls.append("fail")
        self.assertTrue(instance.stop_and_fail_kanban(attempt,"lost",True))
        self.assertEqual(calls,["prepare","stop","mark","recover","fail"])

    def test_memory_gates_reject_noise_secrets_and_weak_org_evidence(self):
        valid = {"content":"Use one stable operation key to prevent duplicate external effects after uncertain submissions.",
                 "sources":["a","b"], "convention":True}
        self.assertTrue(controller.memory_base_gate(valid))
        self.assertTrue(controller.memory_org_gate(valid))
        self.assertFalse(controller.memory_base_gate(dict(valid, content="token=github_pat_abcdefghijklmnopqrstuvwxyz")))
        self.assertFalse(controller.memory_base_gate(dict(valid, sources=["a"])))
        self.assertFalse(controller.memory_org_gate(dict(valid, sources=["a"])))

    def test_publication_helper_preserves_exact_bytes(self):
        helper = ROOT / "bin/doc-writer-publication"
        with tempfile.TemporaryDirectory() as stage, tempfile.TemporaryDirectory() as inbox:
            content = b"exact\x00bytes\n"
            staged = subprocess.run([helper, "stage", "--stage-root", stage, "--request-id", "7", "--name", "publish.md"],
                                    input=content, capture_output=True, check=True)
            binding = json.loads(staged.stdout)
            subprocess.run([helper, "publish", "--stage-root", stage, "--inbox-root", inbox,
                "--staged-path", binding["staged_path"], "--target-path", "dd-2026-01-01-exact-7.md",
                "--digest", binding["content_digest"]], check=True, capture_output=True)
            self.assertEqual((pathlib.Path(inbox) / "dd-2026-01-01-exact-7.md").read_bytes(), content)


if __name__ == "__main__": unittest.main()
