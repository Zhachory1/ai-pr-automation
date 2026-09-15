#!/usr/bin/env python3
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "bin" / "hermes-run"
API_KEY = "0123456789abcdef0123456789abcdef"


class State:
    mode = "completed"
    post_count = 0
    stop_count = 0
    last_body = None
    last_key = None
    redirect_hits = 0
    confirm_stop = False

    @classmethod
    def reset(cls, mode="completed"):
        cls.mode = mode
        cls.post_count = cls.stop_count = cls.redirect_hits = 0
        cls.last_body = cls.last_key = None
        cls.confirm_stop = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        if self.headers.get("Authorization") != f"Bearer {API_KEY}":
            self.send_json(401, {"error": "unauthorized"})
            return False
        return True

    def do_POST(self):
        if not self.authorized():
            return
        if self.path.endswith("/stop"):
            State.stop_count += 1
            if State.confirm_stop:
                State.mode = "cancelled"
            self.send_json(200, {"status": "stopping"})
            return
        State.post_count += 1
        State.last_key = self.headers.get("Idempotency-Key")
        State.last_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if State.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/redirect-target")
            self.end_headers()
        elif State.mode == "conflict":
            self.send_json(409, {"error": {"code": "idempotency_key_conflict"}})
        elif State.mode == "submit_500":
            self.send_json(503, {"error": "uncertain"})
        elif State.mode == "submit_401":
            self.send_json(401, {"error": "unauthorized"})
        elif State.mode == "submit_422":
            self.send_json(422, {"error": "bad request"})
        elif State.mode == "bad_submit":
            self.send_json(202, {"status": "queued"})
        else:
            self.send_json(202, {"run_id": "run-1", "status": "queued"})

    def do_GET(self):
        if self.path == "/redirect-target":
            State.redirect_hits += 1
            self.send_json(200, {"run_id": "leaked"})
            return
        if not self.authorized():
            return
        if State.mode == "poll_500":
            self.send_json(503, {"error": "temporary"})
            return
        if State.mode == "malformed_root":
            self.send_json(200, ["not", "an", "object"])
            return
        if State.mode == "not_found":
            self.send_json(404, {"error": "missing"})
            return
        status = State.mode if State.mode in {
            "running", "waiting_for_approval", "failed", "cancelled", "interrupted", "mystery"
        } else ["invalid"] if State.mode == "malformed_status" else "completed"
        if State.mode == "response_cap":
            output = "x" * (512 * 1024 + 1)
        elif State.mode == "invalid_utf8":
            output = "\ud800"
        else:
            output = "x" * (256 * 1024 + 1) if State.mode == "oversized" else "done"
        self.send_json(200, {
            "status": status,
            "output": output if status == "completed" else None,
            "error": "provider failed" if status == "failed" else None,
            "usage": {"input_tokens": 10, "output_tokens": 2},
        })


class HermesRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        State.reset()

    def run_adapter(self, body=None, *extra, url=None):
        body = body or {
            "input": "task", "instructions": "text only",
            "model": "gpt-5.6-sol", "provider": "openai-api",
        }
        with tempfile.TemporaryDirectory() as directory:
            request_file = pathlib.Path(directory) / "request.json"
            request_file.write_text(json.dumps(body, sort_keys=True, separators=(",", ":")))
            env = os.environ | {"HERMES_DOC_API_KEY": API_KEY}
            return subprocess.run([
                str(ADAPTER), "--url", url or self.url,
                "--request-file", str(request_file), "--idempotency-key", "doc:1:draft",
                "--timeout", "0.05", "--poll-interval", "0.01", *extra,
            ], env=env, text=True, capture_output=True)

    def payload(self, result):
        self.assertTrue(result.stdout.strip(), result.stderr)
        return json.loads(result.stdout)

    def test_submit_and_complete(self):
        result = self.run_adapter()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.payload(result)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["output"], "done")
        self.assertEqual(payload["output_digest"], hashlib.sha256(b"done").hexdigest())
        self.assertEqual(payload["usage"], {"input_tokens": 10, "output_tokens": 2})
        self.assertEqual(State.post_count, 1)
        self.assertEqual(State.last_key, "doc:1:draft")
        self.assertEqual(json.loads(State.last_body)["provider"], "openai-api")

    def test_known_run_id_polls_without_post(self):
        result = self.run_adapter(None, "--run-id", "run-known")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.payload(result)["run_id"], "run-known")
        self.assertEqual(State.post_count, 0)

    def test_conflict_reconciles(self):
        State.mode = "conflict"
        result = self.run_adapter()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.payload(result)["status"], "reconcile")

    def test_request_shape_and_model_are_fixed(self):
        bad = {"input": "x", "instructions": "x", "model": "other", "provider": "openai-api"}
        result = self.run_adapter(bad)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(State.post_count, 0)
        extra = {**bad, "model": "gpt-5.6-sol", "session_id": "forbidden"}
        self.assertEqual(self.run_adapter(extra).returncode, 2)
        self.assertEqual(self.run_adapter(url=self.url + "/prefix").returncode, 2)
        huge = {"input": "x" * (1024 * 1024), "instructions": "x",
                "model": "gpt-5.6-sol", "provider": "openai-api"}
        self.assertEqual(self.run_adapter(huge).returncode, 2)
        self.assertEqual(State.post_count, 0)

    def test_redirect_is_rejected_without_following(self):
        State.mode = "redirect"
        result = self.run_adapter()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.payload(result)["error"], "Hermes redirect rejected")
        self.assertEqual(State.redirect_hits, 0)

    def test_submit_5xx_is_unknown_and_422_is_failed(self):
        State.mode = "submit_500"
        result = self.run_adapter()
        self.assertEqual(result.returncode, 4)
        self.assertEqual(self.payload(result)["status"], "submit_unknown")
        for mode, code in (("submit_401", 401), ("submit_422", 422)):
            State.reset(mode)
            result = self.run_adapter()
            self.assertEqual(result.returncode, 2)
            self.assertEqual(self.payload(result)["raw_status"], f"http_{code}")

    def test_transport_failure_is_submit_unknown(self):
        result = self.run_adapter(url="http://127.0.0.1:1")
        self.assertEqual(result.returncode, 4)
        self.assertEqual(self.payload(result)["status"], "submit_unknown")

    def test_bad_submit_response_reconciles(self):
        State.mode = "bad_submit"
        result = self.run_adapter()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.payload(result)["raw_status"], "invalid_submit_response")

    def test_malformed_poll_responses_reconcile(self):
        for mode in ("malformed_root", "malformed_status"):
            with self.subTest(mode=mode):
                State.reset(mode)
                result = self.run_adapter()
                self.assertEqual(result.returncode, 3)
                self.assertEqual(self.payload(result)["status"], "reconcile")

    def test_unknown_status_reconciles(self):
        State.mode = "mystery"
        result = self.run_adapter()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.payload(result)["error"], "unknown Hermes status")

    def test_waiting_for_approval_stops_and_reconciles(self):
        State.mode = "waiting_for_approval"
        result = self.run_adapter()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(State.stop_count, 1)

    def test_response_and_output_caps_fail_closed(self):
        State.mode = "response_cap"
        result = self.run_adapter()
        self.assertEqual(result.returncode, 3)
        self.assertIn("response exceeds", self.payload(result)["error"])
        State.reset("oversized")
        result = self.run_adapter()
        self.assertEqual(result.returncode, 2)
        self.assertIn("output exceeds", self.payload(result)["error"])
        State.reset("invalid_utf8")
        result = self.run_adapter()
        self.assertEqual(result.returncode, 2)
        self.assertIn("valid UTF-8", self.payload(result)["error"])

    def test_timeout_requests_stop_but_stays_unconfirmed(self):
        State.mode = "running"
        result = self.run_adapter()
        self.assertEqual(result.returncode, 4)
        self.assertEqual(self.payload(result)["status"], "stop_unconfirmed")
        self.assertEqual(State.stop_count, 1)

    def test_timeout_maps_confirmed_cancellation(self):
        State.mode = "running"
        State.confirm_stop = True
        result = self.run_adapter()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.payload(result)["raw_status"], "cancelled")
        self.assertEqual(State.stop_count, 1)

    def test_poll_5xx_retries_until_unconfirmed_stop(self):
        State.mode = "poll_500"
        result = self.run_adapter()
        self.assertEqual(result.returncode, 4)
        self.assertEqual(self.payload(result)["status"], "stop_unconfirmed")
        self.assertEqual(State.stop_count, 1)

    def test_sigterm_requests_stop(self):
        State.mode = "running"
        body = {"input": "task", "instructions": "text only",
                "model": "gpt-5.6-sol", "provider": "openai-api"}
        with tempfile.TemporaryDirectory() as directory:
            request_file = pathlib.Path(directory) / "request.json"
            request_file.write_text(json.dumps(body))
            process = subprocess.Popen([
                str(ADAPTER), "--url", self.url, "--request-file", str(request_file),
                "--idempotency-key", "doc:term:draft", "--timeout", "5",
                "--poll-interval", "0.02",
            ], env=os.environ | {"HERMES_DOC_API_KEY": API_KEY}, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(100):
                if State.post_count:
                    break
                time.sleep(0.01)
            process.terminate()
            stdout, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 4)
        self.assertEqual(json.loads(stdout)["status"], "stop_unconfirmed")
        self.assertEqual(State.stop_count, 1)

    def test_missing_known_run_reconciles_without_post(self):
        State.mode = "not_found"
        result = self.run_adapter(None, "--run-id", "run-missing")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(State.post_count, 0)


if __name__ == "__main__":
    unittest.main()
