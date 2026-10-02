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


class ReviewSubmitTest(unittest.TestCase):
    def test_discovered_review_uses_scoped_key_and_replays_same_run(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), fake.Hermes)
        server.requests = {}; server.redirect_to = ""; server.run_id_prefix = "run-"; server.seen = 0
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                key = Path(directory) / "review-key"
                key.write_text("review-key\n"); key.chmod(0o600)
                env = os.environ | {"HERMES_REVIEW_KEY_FILE": str(key),
                                    "HERMES_API_BASE_URL": f"http://127.0.0.1:{server.server_port}"}
                command = ["python3", "scripts/hermes-review-submit.py", "Owner/Repo", "7", "a" * 40]
                first = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                replay = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                response = json.loads(first.stdout)
                self.assertEqual(response["run_id"], json.loads(replay.stdout)["run_id"])
                self.assertTrue(json.loads(replay.stdout)["replayed"])
                self.assertEqual(server.requests[response["operation_id"]][2], "pr-review-v1")
                self.assertNotIn("review-key", first.stdout + first.stderr)
                changed = subprocess.run([*command[:-1], "b" * 40], cwd=ROOT, env=env,
                                         capture_output=True, text=True, check=True)
                self.assertNotEqual(response["operation_id"], json.loads(changed.stdout)["operation_id"])

                authority = Path(directory) / "authority.yaml"
                authority.write_text("repos:\n  - Owner/Repo\n")
                binary = Path(directory) / "bin"
                binary.mkdir()
                gh = binary / "gh"
                gh.write_text('#!/usr/bin/env bash\n'
                              'if [[ "$1 $2" == "search prs" ]]; then\n'
                              '  printf "Owner/Repo\\t7\\thttps://github.com/Owner/Repo/pull/7\\tReview\\t1700000000\\n"\n'
                              'elif [[ "$1 $2" == "pr view" ]]; then\n'
                              '  printf "%s\\n" "$TEST_HEAD_SHA"\n'
                              'else exit 2; fi\n')
                gh.chmod(0o755)
                env.update({"PATH": str(binary) + os.pathsep + env["PATH"],
                            "HERMES_AUTHORITY_FILE": str(authority),
                            "HERMES_AUTHORITY_BIN": str(ROOT / "scripts/hermes-authority.py"),
                            "HERMES_REVIEW_SUBMIT_BIN": str(ROOT / "scripts/hermes-review-submit.py"),
                            "PR_REVIEW_QUEUE_ENGINE": "api", "TEST_HEAD_SHA": "c" * 40})
                discover = ["bash", "bin/hermes-pr-producer", "review"]
                for _ in range(2):
                    subprocess.run(discover, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                matched = [(operation, json.loads(body)) for operation, (_, body, _) in server.requests.items()
                           if json.loads(json.loads(body)["input"])["head_sha"] == "c" * 40]
                self.assertEqual(len(matched), 1)
                operation, body = matched[0]
                self.assertEqual(body["session_id"], operation)
                self.assertEqual(json.loads(body["input"]), {"repo": "owner/repo", "number": 7,
                                 "head_sha": "c" * 40, "operation_id": operation})
                env["TEST_HEAD_SHA"] = "d" * 40
                subprocess.run(discover, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                self.assertEqual(len(server.requests), 4)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__": unittest.main()
