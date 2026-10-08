#!/usr/bin/env python3
import hashlib
import hmac
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("kanban_submit", ROOT / "scripts/hermes-kanban-submit.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class Response:
    status = 200
    def __init__(self, body): self.body = json.dumps(body).encode()
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def read(self, _limit): return self.body


class SubmitTest(unittest.TestCase):
    def test_scoped_key_and_exact_response(self):
        with tempfile.TemporaryDirectory() as directory:
            key = pathlib.Path(directory) / "key"
            key.write_text("a" * 64)
            response = {"kind": "pr-review", "board": "pr-review", "operation_id": client.identity("pr-review", "owner/repo", 7, "b" * 40)["operation_id"],
                        "status": "ready", "task_id": "t_12345678"}
            opener = mock.Mock()
            opener.open.return_value = Response(response)
            with mock.patch.dict(client.os.environ, {"HERMES_KANBAN_INGRESS_KEY_FILE": str(key)}, clear=False), \
                 mock.patch.object(client.urllib.request, "build_opener", return_value=opener):
                self.assertEqual(client.submit("pr-review", "owner/repo", 7, "Fix", "b" * 40), response)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.full_url, "http://host.docker.internal:8767/v1/pr-tasks/pr-review")
                headers = {name.lower(): value for name, value in request.header_items()}
                self.assertNotIn("authorization", headers)
                stamp = headers["x-hermes-timestamp"]
                signature = hmac.new(bytes.fromhex("a" * 64),
                    b"POST\n/v1/pr-tasks/pr-review\n" + stamp.encode() + b"\n" + request.data, hashlib.sha256).hexdigest()
                self.assertEqual(headers["x-hermes-signature"], signature)
                self.assertEqual(json.loads(request.data), {"repo": "owner/repo", "number": 7,
                                 "url": "https://github.com/owner/repo/pull/7", "title": "Fix", "head_sha": "b" * 40})
                opener.open.return_value = Response({**response, "task_id": None})
                with self.assertRaises(ValueError): client.submit("pr-review", "owner/repo", 7, "Fix", "b" * 40)
                deferred = {"kind": "pr-maintain", "board": "pr-maintain", "operation_id":
                            client.identity("pr-maintain", "owner/repo", 7, "b" * 40, "1" * 64)["operation_id"],
                            "status": "deferred", "task_id": None}
                opener.open.return_value = Response(deferred)
                self.assertEqual(client.submit("pr-maintain", "owner/repo", 7, "Fix", "b" * 40, "1" * 64), deferred)
                opener.open.return_value = Response({**deferred, "operation_id": "pr-maintain-" + "0" * 64})
                with self.assertRaisesRegex(ValueError, "invalid Kanban ingress result"):
                    client.submit("pr-maintain", "owner/repo", 7, "Fix", "b" * 40, "1" * 64)
                with mock.patch.dict(client.os.environ, {"HERMES_KANBAN_INGRESS_URL": "http://example.com:8767"}):
                    with self.assertRaises(ValueError): client.submit("pr-review", "owner/repo", 7, "Fix", "b" * 40)


    def test_unresolved_create_error_identifies_fenced_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            key = pathlib.Path(directory) / "key"
            key.write_text("a" * 64)
            error = client.urllib.error.HTTPError("http://host.docker.internal:8767", 400, "admission failed", None,
                                                  io.BytesIO(b'{"error":"unresolved create outcome"}'))
            opener = mock.Mock()
            opener.open.side_effect = error
            operation = client.identity("pr-review", "owner/repo", 7, "b" * 40)["operation_id"]
            with mock.patch.dict(client.os.environ, {"HERMES_KANBAN_INGRESS_KEY_FILE": str(key)}, clear=False), \
                 mock.patch.object(client.urllib.request, "build_opener", return_value=opener):
                with self.assertRaisesRegex(ValueError, f"{operation}.*unresolved create outcome"):
                    client.submit("pr-review", "owner/repo", 7, "Fix", "b" * 40)
            opener.open.assert_called_once()
            opener.open.reset_mock()
            prior = "pr-maintain-" + "c" * 64
            opener.open.side_effect = client.urllib.error.HTTPError(
                "http://host.docker.internal:8767", 400, "prior create uncertain", None,
                io.BytesIO(json.dumps({"error": "unresolved create outcome for " + prior}).encode()))
            with mock.patch.dict(client.os.environ, {"HERMES_KANBAN_INGRESS_KEY_FILE": str(key)}, clear=False), \
                 mock.patch.object(client.urllib.request, "build_opener", return_value=opener):
                with self.assertRaisesRegex(ValueError, prior):
                    client.submit("pr-maintain", "owner/repo", 7, "Fix", "b" * 40, "1" * 64)
            opener.open.assert_called_once()
            opener.open.reset_mock()
            opener.open.side_effect = client.urllib.error.HTTPError(
                "http://host.docker.internal:8767", 400, "prior request incomplete", None,
                io.BytesIO(json.dumps({"error": "prior maintenance request incomplete for " + prior}).encode()))
            with mock.patch.dict(client.os.environ, {"HERMES_KANBAN_INGRESS_KEY_FILE": str(key)}, clear=False), \
                 mock.patch.object(client.urllib.request, "build_opener", return_value=opener):
                with self.assertRaisesRegex(ValueError, prior):
                    client.submit("pr-maintain", "owner/repo", 7, "Fix", "b" * 40, "1" * 64)
            opener.open.assert_called_once()
            opener.open.reset_mock()
            opener.open.side_effect = client.urllib.error.HTTPError(
                "http://host.docker.internal:8767", 503, "admission busy", None,
                io.BytesIO(b'{"error":"ingress busy"}'))
            with mock.patch.dict(client.os.environ, {"HERMES_KANBAN_INGRESS_KEY_FILE": str(key)}, clear=False), \
                 mock.patch.object(client.urllib.request, "build_opener", return_value=opener):
                with self.assertRaisesRegex(ValueError, f"{operation}.*ingress busy"):
                    client.submit("pr-review", "owner/repo", 7, "Fix", "b" * 40)
            opener.open.assert_called_once()


if __name__ == "__main__": unittest.main()
