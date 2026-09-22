#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


config = load("configure_hermes_api", ROOT / "scripts/configure-hermes-api.py")
conformance = load("hermes_api_conformance", ROOT / "scripts/hermes-api-conformance.py")


class FakeHermes(BaseHTTPRequestHandler):
    keys = {name: f"profile-key-{index:02d}-1111111111111111111111111111"
            for index, name in enumerate(config.PROFILES, 1)}
    default_key = "default-key-000000000000000000000000000"
    runs = {}

    def log_message(self, *_): pass

    def reply(self, status, body, headers=None):
        raw = json.dumps(body).encode(); self.send_response(status)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers(); self.wfile.write(raw)

    def profile(self):
        parts = self.path.split("/"); return parts[2] if len(parts) > 3 and parts[1] == "p" else None

    def authorized(self): return self.headers.get("Authorization") == f"Bearer {self.keys.get(self.profile())}"

    def do_GET(self):
        if not self.authorized(): return self.reply(401, {"error": "unauthorized"})
        if self.path.endswith("/v1/models"): return self.reply(200, {"data": []})
        run_id = self.path.rsplit("/", 1)[-1]
        if run_id in self.runs: return self.reply(200, {"object":"hermes.run","run_id": run_id,
            "status":"completed","created_at":1.0,"updated_at":2.0,"last_event":"run.completed",
            "session_id":"s","model":"m","output":"CONFORMANCE_OK",
            "usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}})
        self.reply(404, {})

    def do_POST(self):
        if not self.authorized(): return self.reply(401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length", "0")); body = self.rfile.read(length)
        key = self.headers.get("Idempotency-Key")
        if key in self.runs:
            run_id, previous = self.runs[key]
            if previous != body: return self.reply(409, {"error": "conflict"})
            return self.reply(202, {"run_id": run_id, "status": "completed", "replayed": True}, {"Idempotency-Replayed": "true"})
        run_id = "run_test"; self.runs[key] = (run_id, body); self.runs[run_id] = True
        self.reply(202, {"run_id": run_id, "status": "started", "replayed": False})


class FlakyAuthClient:
    def __init__(self, keys):
        self.keys = keys; self.ready_timeouts = set(keys); self.auth_timeout = True; self.correct_seen = set()

    def request(self, method, path, key):
        profile = path.split("/")[2]
        if key == self.keys[profile]:
            if profile in self.ready_timeouts:
                self.ready_timeouts.remove(profile); raise TimeoutError("profile warming")
            self.correct_seen.add(profile); return 200, {}, {}
        if self.auth_timeout:
            self.auth_timeout = False; raise TimeoutError("auth route warming")
        return 401, {}, {}


class FoundationTest(unittest.TestCase):
    def test_readiness_warms_every_profile_and_auth_retries_timeout(self):
        client = FlakyAuthClient(FakeHermes.keys)
        keys = {"profiles": FakeHermes.keys}
        conformance.wait_ready(client, keys, 5)
        self.assertEqual(client.correct_seen, set(FakeHermes.keys))
        conformance.auth_probe(client, keys, 2)

    def test_key_bundle_is_stable_unique_and_private(self):
        with tempfile.TemporaryDirectory() as td:
            parent = pathlib.Path(td) / "private"; parent.mkdir(mode=0o700)
            path = parent / "keys.json"
            first = config.load_or_create(path); second = config.load_or_create(path)
            self.assertEqual(first, second)
            keys = list(first["profiles"].values())
            self.assertEqual(len(keys), len(set(keys)))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(set(first["profiles"]), set(config.PROFILES))

    def test_env_rewrite_preserves_unmanaged_values(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / ".env"; path.write_text("GH_TOKEN=keep\nAPI_SERVER_KEY=old\n")
            config.rewrite_env(path, {"API_SERVER_KEY": "new", "API_SERVER_ENABLED": "true"}, os.getuid(), os.getgid())
            self.assertEqual(path.read_text(), "GH_TOKEN=keep\nAPI_SERVER_KEY=new\nAPI_SERVER_ENABLED=true\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_auth_replay_conflict_and_terminal_poll(self):
        FakeHermes.runs = {}
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHermes)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                keys = {"schema_version": 1, "auth_generation": 1, "profiles": FakeHermes.keys}
                path = pathlib.Path(td) / "keys.json"; path.write_text(json.dumps(keys))
                result = subprocess.run([
                    sys.executable, str(ROOT / "scripts/hermes-api-conformance.py"),
                    "--base-url", f"http://127.0.0.1:{server.server_port}",
                    "--keys-file", str(path), "--run-profile", "pr-review-v1", "--run-timeout", "5"],
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("auth conformance passed", result.stdout)
                self.assertIn("run conformance passed", result.stdout)
        finally:
            server.shutdown(); server.server_close()


if __name__ == "__main__": unittest.main()
