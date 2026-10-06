#!/usr/bin/env python3
import importlib.util
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
                self.assertEqual(request.get_header("Authorization"), "Bearer " + "a" * 64)
                self.assertEqual(json.loads(request.data), {"repo": "owner/repo", "number": 7,
                                 "url": "https://github.com/owner/repo/pull/7", "title": "Fix", "head_sha": "b" * 40})
                opener.open.return_value = Response({**response, "task_id": None})
                with self.assertRaises(ValueError): client.submit("pr-review", "owner/repo", 7, "Fix", "b" * 40)
                with mock.patch.dict(client.os.environ, {"HERMES_KANBAN_INGRESS_URL": "http://example.com:8767"}):
                    with self.assertRaises(ValueError): client.submit("pr-review", "owner/repo", 7, "Fix", "b" * 40)


if __name__ == "__main__": unittest.main()
