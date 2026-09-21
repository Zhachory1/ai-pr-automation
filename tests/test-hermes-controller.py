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
        self.assertEqual(controller.valid_generic("pr-review", value, nonce, {}, f"o/r#1@{head}"), value)
        self.assertIsNone(controller.valid_generic("pr-review", dict(value, extra=True), nonce, {}, f"o/r#1@{head}"))
        self.assertIsNone(controller.valid_generic("pr-review", dict(value, posted_ref="wrong"), nonce, {}, f"o/r#1@{head}"))

    def test_safety_clear_is_incident_free_and_identity_bound(self):
        payload = {"operation_id":"op","repo":"o/r","pr":1,"head_sha":"h","base_sha":"b",
                   "diff_hash":"d","policy_version":"v1","policy_digest":"p"}
        value = dict(payload, status="clear", intent={}, findings=[], coverage={}, documentation={},
                     observability={}, incident={"candidate":False}, human_decisions_needed=[])
        self.assertTrue(controller.valid_safety(value, payload))
        self.assertFalse(controller.valid_safety(dict(value, findings=[{"claim":"x"}]), payload))
        self.assertFalse(controller.valid_safety(dict(value, incident={"candidate":True}), payload))

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
