#!/usr/bin/env python3
import importlib
import json
import pathlib
import sys
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
client = importlib.import_module("hermes_run_request")


class Hermes(BaseHTTPRequestHandler):
    keys = {"pr-review-v1": "review-key", "pr-maintain-v1": "maintain-key"}

    def log_message(self, *_): pass

    def reply(self, code, value):
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def route(self):
        parts = self.path.split("/")
        if len(parts) < 5 or parts[1] != "p" or parts[3:5] != ["v1", "runs"]:
            return None
        profile = parts[2]
        if self.headers.get("Authorization") != f"Bearer {self.keys.get(profile)}":
            return None
        return profile

    def do_POST(self):
        self.server.seen += 1
        if self.server.redirect_to:
            self.send_response(307)
            self.send_header("Location", self.server.redirect_to)
            self.end_headers()
            return
        profile = self.route()
        if not profile or not self.path.endswith("/v1/runs"):
            return self.reply(401, {})
        key = self.headers.get("Idempotency-Key")
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        existing = self.server.requests.get(key)
        if existing and existing[1] != body:
            return self.reply(409, {"error": "conflict"})
        if existing:
            return self.reply(202, {"run_id": existing[0], "status": "completed", "replayed": True})
        run_id = f"{self.server.run_id_prefix}{len(self.server.requests) + 1}"
        self.server.requests[key] = (run_id, body, profile)
        self.reply(202, {"run_id": run_id, "status": "started", "replayed": False})

    def do_GET(self):
        self.server.seen += 1
        profile = self.route()
        if not profile or not self.path.startswith(f"/p/{profile}/v1/runs/"):
            return self.reply(401, {})
        self.server.requested_path = self.path
        run_id = urllib.parse.unquote(self.path.rsplit("/", 1)[-1])
        if not any(item[0] == run_id for item in self.server.requests.values()):
            return self.reply(404, {})
        self.reply(200, {"object": "hermes.run", "run_id": run_id, "status": "completed"})


class RequestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Hermes)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.requests = {}
        self.server.redirect_to = ""
        self.server.run_id_prefix = "run-"
        self.server.seen = 0

    def test_review_replay_new_head_and_run_status(self):
        head = "a" * 40
        first = client.submit("pr-review", "Owner/Repo", 7, head, "review-key", base_url=self.url)
        replay = client.submit("pr-review", "owner/repo", 7, head, "review-key", base_url=self.url)
        self.assertEqual((first["run_id"], replay["run_id"], replay["replayed"]), ("run-1", "run-1", True))
        request = self.server.requests[first["operation_id"]]
        self.assertEqual(request[2], "pr-review-v1")
        body = json.loads(request[1])
        self.assertEqual(json.loads(body["input"]), {"operation_id": first["operation_id"],
                         "repo": "owner/repo", "number": 7, "head_sha": head})
        self.assertEqual(body["session_id"], first["operation_id"])
        self.assertIn(first["operation_id"][-32:], body["instructions"])
        changed = client.submit("pr-review", "Owner/Repo", 7, "b" * 40, "review-key", base_url=self.url)
        self.assertNotEqual(first["operation_id"], changed["operation_id"])
        self.assertEqual(changed["run_id"], "run-2")
        self.assertEqual(client.status("pr-review", first["run_id"], "review-key", base_url=self.url),
                         {"run_id": "run-1", "status": "completed"})

    def test_maintenance_binds_head_and_feedback(self):
        first = client.submit("pr-maintain", "Owner/Repo", 7, "a" * 40, "maintain-key",
                              feedback_digest="b" * 64, base_url=self.url)
        self.assertEqual(self.server.requests[first["operation_id"]][2], "pr-maintain-v1")
        self.assertEqual(json.loads(json.loads(self.server.requests[first["operation_id"]][1])["input"]),
                         {"operation_id": first["operation_id"], "repo": "owner/repo", "number": 7,
                          "head_sha": "a" * 40, "feedback_digest": "b" * 64})
        same_feedback_new_head = client.submit("pr-maintain", "Owner/Repo", 7, "c" * 40, "maintain-key",
                                               feedback_digest="b" * 64, base_url=self.url)
        self.assertNotEqual(first["operation_id"], same_feedback_new_head["operation_id"])
        new_feedback = client.submit("pr-maintain", "Owner/Repo", 7, "c" * 40, "maintain-key",
                                     feedback_digest="d" * 64, base_url=self.url)
        self.assertEqual(len(self.server.requests), 3)
        self.assertNotEqual(new_feedback["operation_id"], same_feedback_new_head["operation_id"])

    def test_opaque_run_id_is_escaped_for_status(self):
        self.server.run_id_prefix = "run/"
        submitted = client.submit("pr-review", "Owner/Repo", 7, "a" * 40, "review-key", base_url=self.url)
        self.assertEqual(submitted["run_id"], "run/1")
        self.assertEqual(client.status("pr-review", submitted["run_id"], "review-key", base_url=self.url)["status"],
                         "completed")
        self.assertTrue(self.server.requested_path.endswith("run%2F1"))

    def test_rejects_redirect_without_forwarding_bearer_key(self):
        target = ThreadingHTTPServer(("127.0.0.1", 0), Hermes)
        target.requests = {}; target.redirect_to = ""; target.run_id_prefix = "run-"; target.seen = 0
        thread = threading.Thread(target=target.serve_forever, daemon=True); thread.start()
        try:
            self.server.redirect_to = f"http://127.0.0.1:{target.server_port}/steal"
            with self.assertRaisesRegex(ValueError, "HTTP 307"):
                client.submit("pr-review", "Owner/Repo", 7, "a" * 40, "review-key", base_url=self.url)
            self.assertEqual(target.seen, 0)
        finally:
            target.shutdown(); target.server_close(); thread.join()

    def test_rejects_changed_body_and_nonlocal_gateway(self):
        first = client.submit("pr-review", "Owner/Repo", 7, "a" * 40, "review-key", base_url=self.url)
        with self.assertRaisesRegex(ValueError, "HTTP 409"):
            client._request(self.url, "pr-review", "review-key", "POST", body={"input": "changed"},
                            operation_id=first["operation_id"])
        with self.assertRaisesRegex(ValueError, "local host gateway"):
            client.submit("pr-review", "Owner/Repo", 7, "a" * 40, "review-key",
                          base_url="https://example.com")
        self.assertEqual(len(self.server.requests), 1)


if __name__ == "__main__": unittest.main()
