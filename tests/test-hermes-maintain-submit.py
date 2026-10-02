#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fake_hermes_runs", ROOT / "tests/test-hermes-run-request.py")
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)


class MaintainSubmitTest(unittest.TestCase):
    def test_maintenance_key_head_feedback_and_replay(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), fake.Hermes)
        server.requests = {}; server.redirect_to = ""; server.run_id_prefix = "run-"; server.seen = 0
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                key = Path(directory) / "maintain-key"
                key.write_text("maintain-key\n"); key.chmod(0o600)
                env = os.environ | {"HERMES_MAINTAIN_KEY_FILE": str(key),
                                    "HERMES_API_BASE_URL": f"http://127.0.0.1:{server.server_port}"}
                command = ["python3", "scripts/hermes-maintain-submit.py", "Owner/Repo", "7",
                           "a" * 40, "b" * 64]
                def invoke(arguments):
                    return json.loads(subprocess.run(arguments, cwd=ROOT, env=env,
                                                     capture_output=True, text=True, check=True).stdout)
                first = invoke(command)
                replay = invoke(command)
                self.assertEqual(first["run_id"], replay["run_id"])
                self.assertTrue(replay["replayed"])
                record = server.requests[first["operation_id"]]
                self.assertEqual(record[2], "pr-maintain-v1")
                self.assertEqual(json.loads(json.loads(record[1])["input"]),
                                 {"repo": "owner/repo", "number": 7, "head_sha": "a" * 40,
                                  "feedback_digest": "b" * 64, "operation_id": first["operation_id"]})
                self.assertNotEqual(first["operation_id"], invoke([*command[:-2], "c" * 40, "b" * 64])["operation_id"])
                self.assertNotEqual(first["operation_id"], invoke([*command[:-1], "d" * 64])["operation_id"])
                self.assertEqual(len(server.requests), 3)

                server.requests.clear()
                authority = Path(directory) / "authority.yaml"
                authority.write_text("repos:\n  - Owner/Repo\n")
                binary = Path(directory) / "bin"
                binary.mkdir()
                gh = binary / "gh"
                gh.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["search", "prs"]:
    print("Owner/Repo\\t7\\thttps://github.com/Owner/Repo/pull/7\\tMaintain\\t1700000000")
elif args[:2] == ["pr", "view"]:
    print(os.environ["TEST_HEAD_SHA"])
elif args[:2] == ["api", "user"]:
    print("maintainer")
elif args[:2] == ["api", "--paginate"]:
    version = os.environ["FEEDBACK_VERSION"]
    print(json.dumps([] if version == "none" else [{"id": 10, "user": {"login": "reviewer"},
                                                      "body": "review-" + version}]))
elif args[:2] == ["api", "graphql"]:
    print(json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}))
elif args[:2] == ["pr", "checks"]:
    print("[]")
else:
    raise SystemExit(2)
''')
                gh.chmod(0o755)
                psql = binary / "psql"
                psql.write_text('#!/bin/sh\ntouch "$TEST_PSQL_LOG"\nexit 2\n')
                psql.chmod(0o755)
                env.update({"PATH": str(binary) + os.pathsep + env["PATH"],
                            "HERMES_AUTHORITY_FILE": str(authority),
                            "HERMES_AUTHORITY_BIN": str(ROOT / "scripts/hermes-authority.py"),
                            "HERMES_MAINTAIN_SUBMIT_BIN": str(ROOT / "scripts/hermes-maintain-submit.py"),
                            "PR_MAINTAIN_QUEUE_ENGINE": "api", "TEST_HEAD_SHA": "a" * 40,
                            "FEEDBACK_VERSION": "v1", "TEST_PSQL_LOG": str(Path(directory) / "psql.log"),
                            "TMPDIR": directory})
                discover = ["bash", "bin/hermes-pr-producer", "maintain"]
                for _ in range(2):
                    subprocess.run(discover, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                self.assertEqual(len(server.requests), 1)
                operation, (_, raw, profile) = next(iter(server.requests.items()))
                self.assertEqual(profile, "pr-maintain-v1")
                self.assertEqual(json.loads(raw)["session_id"], operation)
                self.assertEqual(json.loads(json.loads(raw)["input"])["head_sha"], "a" * 40)
                env["FEEDBACK_VERSION"] = "v2"
                subprocess.run(discover, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                self.assertEqual(len(server.requests), 2)
                env.update({"TEST_HEAD_SHA": "c" * 40, "FEEDBACK_VERSION": "v1"})
                subprocess.run(discover, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                self.assertEqual(len(server.requests), 3)
                env["FEEDBACK_VERSION"] = "v3"
                subprocess.run(discover, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                self.assertEqual(len(server.requests), 4)
                env["FEEDBACK_VERSION"] = "none"
                subprocess.run(discover, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                self.assertEqual(len(server.requests), 4)
                self.assertFalse(Path(env["TEST_PSQL_LOG"]).exists())
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__": unittest.main()
