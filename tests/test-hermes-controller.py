#!/usr/bin/env python3
import importlib.util
import json
import pathlib
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.modules.setdefault("psycopg", types.SimpleNamespace(connect=None))
sys.modules.setdefault("psycopg.rows", types.SimpleNamespace(dict_row=None))
spec = importlib.util.spec_from_file_location("hermes_controller", ROOT / "scripts/hermes-controller.py")
controller = importlib.util.module_from_spec(spec); spec.loader.exec_module(controller)


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


class ControllerContractTest(unittest.TestCase):
    def council_package(self, verdict="clear"):
        return {"workflow_id":"workflow","artifact_digest":"a"*64,"verdict":verdict,"intent":{},
                "findings":[],"coverage":{},"documentation":{},"observability":{},
                "incident":{"candidate":False,"changed_line_cause":False,"concrete_trigger":False,
                            "severe_impact":False,"high_confidence_chain":False,
                            "stop_rollback_or_page":False,"evidence":[]},
                "human_decisions_needed":[],"dissent":[],"residual_risk":[]}

    def safety_payload(self):
        return {"operation_id":"op","repo":"o/r","pr":1,"head_sha":"h","base_sha":"b",
                "diff_hash":"d","policy_version":"v1","policy_digest":"p"}

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
            import subprocess
            staged = subprocess.run([helper, "stage", "--stage-root", stage, "--request-id", "7", "--name", "publish.md"],
                                    input=content, capture_output=True, check=True)
            binding = json.loads(staged.stdout)
            subprocess.run([helper, "publish", "--stage-root", stage, "--inbox-root", inbox,
                "--staged-path", binding["staged_path"], "--target-path", "dd-2026-01-01-exact-7.md",
                "--digest", binding["content_digest"]], check=True, capture_output=True)
            self.assertEqual((pathlib.Path(inbox) / "dd-2026-01-01-exact-7.md").read_bytes(), content)


if __name__ == "__main__": unittest.main()
