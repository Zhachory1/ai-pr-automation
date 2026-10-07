#!/usr/bin/env python3
import importlib.util
import json
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kanban_ingress", ROOT / "scripts/hermes-kanban-ingress.py")
ingress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingress)


class IngressTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir(mode=0o700)
        self.key = self.root / "key"
        self.key.write_text("a" * 64 + "\n")
        self.key.chmod(0o600)
        self.authority = self.root / "authority.yaml"
        self.authority.write_text("repos:\n  - owner/repo\n")
        self.config = ingress.Config(self.work, self.authority, self.root, self.root / "hermes", {"pr-review": "a" * 64, "pr-maintain": "b" * 64})
        self.review = {"repo": "owner/repo", "number": 7, "url": "https://github.com/owner/repo/pull/7", "title": "Fix", "head_sha": "a" * 40}

    def test_key_permissions_fail_closed(self):
        self.assertEqual(ingress.read_key(self.key), "a" * 64)
        self.key.chmod(0o644)
        with self.assertRaises(ValueError):
            ingress.read_key(self.key)

    def test_auth_scope_and_shape_fail_before_helper(self):
        with mock.patch.object(ingress, "invoke") as helper:
            for kind, key, payload in (
                ("pr-review", "wrong", self.review),
                ("pr-maintain", "a" * 64, {**self.review, "feedback_digest": "1" * 64}),
                ("pr-review", "a" * 64, {**self.review, "repo": "other/repo", "url": "https://github.com/other/repo/pull/7"}),
                ("pr-review", "a" * 64, {**self.review, "number": True}),
                ("pr-review", "a" * 64, {**self.review, "extra": "x"}),
            ):
                with self.assertRaises(ValueError):
                    ingress.admit(self.config, kind, key, payload)
            helper.assert_not_called()

    def test_overlapping_kinds_wait_for_the_admission_lock(self):
        entered = threading.Event()
        release = threading.Event()
        results = []

        def helper(config, kind, payload):
            if kind == "pr-review":
                entered.set()
                self.assertTrue(release.wait(2))
            return {"kind": kind, "board": kind, "operation_id": payload["operation_id"],
                    "task_id": "t_12345678", "status": "ready"}

        with mock.patch.object(ingress, "invoke", side_effect=helper) as invoked:
            first = threading.Thread(target=lambda: results.append(ingress.admit(
                self.config, "pr-review", "a" * 64, self.review)), daemon=True)
            first.start()
            self.assertTrue(entered.wait(2))
            timer = threading.Timer(0.2, release.set)
            timer.start()
            try:
                second = ingress.admit(self.config, "pr-maintain", "b" * 64,
                                       {**self.review, "feedback_digest": "1" * 64})
            finally:
                release.set()
                first.join(2)
                timer.join(2)
            self.assertFalse(first.is_alive())
            self.assertEqual((second["kind"], len(results), invoked.call_count), ("pr-maintain", 1, 2))

    def test_review_replay_is_exact_and_maintenance_round_is_capped(self):
        responses = []
        def helper(config, kind, payload):
            responses.append(payload)
            return {"kind": kind, "board": kind, "operation_id": payload["operation_id"], "task_id": "t_12345678", "status": "ready"}
        with mock.patch.object(ingress, "invoke", side_effect=helper):
            first = ingress.admit(self.config, "pr-review", "a" * 64, self.review)
            self.assertEqual(first, ingress.admit(self.config, "pr-review", "a" * 64, self.review))
            self.assertEqual(responses[0], responses[1])
            for index in range(1, 4):
                payload = {**self.review, "feedback_digest": f"{index}" * 64}
                admitted = ingress.admit(self.config, "pr-maintain", "b" * 64, payload)
                self.assertEqual(responses[-1]["round"], index)
                self.assertEqual(admitted["status"], "ready")
                entry = self.work / admitted["operation_id"]
                entry.mkdir(mode=0o700)
                record = entry / "request.json"
                record.write_text(json.dumps(responses[-1], sort_keys=True, separators=(",", ":")))
                record.chmod(0o440)
            replay = ingress.admit(self.config, "pr-maintain", "b" * 64, {**self.review, "feedback_digest": "1" * 64, "head_sha": "b" * 40})
            self.assertEqual(replay["operation_id"], responses[2]["operation_id"])
            capped = ingress.admit(self.config, "pr-maintain", "b" * 64, {**self.review, "feedback_digest": "4" * 64})
            self.assertEqual(capped["status"], "capped")
            self.assertIsNone(capped["task_id"])
            self.assertEqual(len(responses), 6)

    def test_review_title_edit_reuses_stored_request(self):
        operation = ingress.identity("pr-review", "owner/repo", 7, "a" * 40)["operation_id"]
        previous = {"operation_id": operation, **self.review}
        workspace = self.work / operation
        workspace.mkdir(mode=0o700)
        request_path = workspace / "request.json"
        request_path.write_bytes(ingress.ENQUEUE.canonical(previous))
        request_path.chmod(0o440)
        with mock.patch.object(ingress, "invoke", return_value={"kind": "pr-review", "board": "pr-review",
                "operation_id": operation, "task_id": "t_12345678", "status": "done"}) as helper:
            result = ingress.admit(self.config, "pr-review", "a" * 64, {**self.review, "title": "Renamed"})
        self.assertEqual(result["operation_id"], operation)
        self.assertEqual(helper.call_args.args[2], previous)
        self.assertEqual(request_path.read_bytes(), ingress.ENQUEUE.canonical(previous))

    def test_http_auth_and_origin_reject_before_admission(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ingress.Handler)
        prior = ingress.STATE
        ingress.STATE = self.config
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: setattr(ingress, "STATE", prior))
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/v1/pr-tasks/pr-review"
        body = json.dumps(self.review).encode()
        def request(key, origin=None):
            headers = {"Host": "host.docker.internal:8767", "Content-Type": "application/json", "Authorization": "Bearer " + key}
            if origin: headers["Origin"] = origin
            return urllib.request.Request(url, data=body, headers=headers, method="POST")
        with mock.patch.object(ingress, "invoke", return_value={"kind": "pr-review", "board": "pr-review",
                "operation_id": ingress.identity("pr-review", "owner/repo", 7, "a" * 40)["operation_id"],
                "task_id": "t_12345678", "status": "ready"}) as helper:
            for req, expected in ((request("bad"), 401), (request("a" * 64, "https://example.com"), 403)):
                with self.assertRaises(urllib.error.HTTPError) as response:
                    urllib.request.urlopen(req, timeout=3)
                self.assertEqual(response.exception.code, expected)
                response.exception.close()
            helper.assert_not_called()
            with urllib.request.urlopen(request("a" * 64), timeout=3) as response:
                self.assertEqual(response.status, 200)
            helper.assert_called_once()


if __name__ == "__main__":
    unittest.main()
