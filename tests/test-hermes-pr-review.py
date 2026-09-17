#!/usr/bin/env python3
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
API_KEY = "0123456789abcdef0123456789abcdef"


class HermesState:
    requests = {}
    state_dir = None


class HermesHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/v1/runs" or self.headers.get("Authorization") != f"Bearer {API_KEY}":
            self.send_json(404, {})
            return
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))))
        request_data = json.loads(body["input"].removeprefix("<untrusted-pr-data>\n").removesuffix("\n</untrusted-pr-data>"))
        pr = int(request_data["metadata"]["number"])
        run_id = f"run-{pr}"
        outputs = {
            1: {"verdict": "comment", "findings": [{"file": "a.py", "line": 2, "severity": "minor", "text": "Small issue", "blocks_merge": False}], "summary": "Comment summary"},
            2: {"verdict": "request-changes", "findings": [{"file": "b.py", "line": 4, "severity": "critical", "text": "Blocking issue", "blocks_merge": True}], "summary": "Changes needed"},
            3: {"verdict": "approve", "findings": [], "summary": "Clean"},
            4: {"verdict": "comment", "findings": [], "summary": "Stale"},
            5: {"verdict": "approve", "findings": [], "summary": "Self approval"},
            8: {"verdict": "approve", "findings": [], "summary": "Incomplete diff"},
            9: {"verdict": "comment", "findings": [{"file": "c.py", "line": 1, "severity": "critical", "text": "Blocks", "blocks_merge": True}], "summary": "Wrong event"},
            10: {"verdict": "request-changes", "findings": [{"file": "d.py", "line": 1, "severity": "minor", "text": "Nonblocking", "blocks_merge": False}], "summary": "Wrong event"},
            11: {"verdict": "approve", "findings": [], "summary": "Marker mismatch"},
            12: {"verdict": "approve", "findings": [], "summary": "Unknown post"},
            13: {"verdict": "approve", "findings": [], "summary": "Accepted unknown post"},
            14: {"verdict": "approve", "findings": [], "summary": "Moved base"},
            15: {"verdict": "approve", "findings": [], "summary": "Second object"},
        }
        if pr == 6:
            output = "not-json"
        elif pr == 15:
            output = json.dumps(outputs[2], separators=(",", ":")) + json.dumps(outputs[15], separators=(",", ":"))
        else:
            output = json.dumps(outputs[pr], separators=(",", ":"))
        HermesState.requests[pr] = {
            "key": self.headers.get("Idempotency-Key"), "body": body, "output": output,
            "input": request_data,
        }
        if pr == 4:
            (HermesState.state_dir / "head-4").write_text("f" * 40)
        if pr == 11:
            (HermesState.state_dir / "accepted-11.json").write_text(json.dumps({
                "event": "COMMENT", "commit_id": f"{pr:040d}",
                "body": f"wrong event\n\n<!-- ai-pr-automation head={pr:040d} -->",
            }))
        self.send_json(202, {"run_id": run_id, "status": "queued"})

    def do_GET(self):
        if not self.path.startswith("/v1/runs/run-"):
            self.send_json(404, {})
            return
        pr = int(self.path.rsplit("-", 1)[1])
        self.send_json(200, {"status": "completed", "output": HermesState.requests[pr]["output"], "usage": {"input_tokens": 10, "output_tokens": 5}})


class HermesPrReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("docker") or not shutil.which("psql"):
            raise unittest.SkipTest("docker and psql are required")
        cls.temp = tempfile.TemporaryDirectory()
        cls.tmp = pathlib.Path(cls.temp.name)
        HermesState.state_dir = cls.tmp
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), HermesHandler)
        cls.http_thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.http_thread.start()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.db_port = sock.getsockname()[1]
        cls.container = f"hermes-pr-review-test-{os.getpid()}"
        subprocess.run([
            "docker", "run", "--rm", "-d", "--name", cls.container,
            "-e", "POSTGRES_PASSWORD=t", "-e", "POSTGRES_DB=fleet",
            "-p", f"{cls.db_port}:5432", "postgres:16",
        ], check=True, stdout=subprocess.DEVNULL)
        for _ in range(30):
            if subprocess.run(["docker", "exec", cls.container, "pg_isready", "-U", "postgres"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("postgres did not start")
        time.sleep(1)
        for name in ("01-schema.sql", "02-agent-server.sql", "03-human-review-queue.sql"):
            source = ROOT / "docker" / "initdb" / name
            subprocess.run(["docker", "cp", str(source), f"{cls.container}:/tmp/{name}"], check=True)
            subprocess.run(["docker", "exec", cls.container, "psql", "-U", "postgres", "-d", "fleet",
                            "-q", "-v", "ON_ERROR_STOP=1", "-f", f"/tmp/{name}"], check=True)
        cls.make_fake_gh()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        subprocess.run(["docker", "rm", "-f", cls.container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.temp.cleanup()

    @classmethod
    def make_fake_gh(cls):
        bindir = cls.tmp / "bin"
        bindir.mkdir()
        script = bindir / "gh"
        script.write_text(r'''#!/usr/bin/env bash
set -euo pipefail
head_for() { printf '%040d' "$1"; }
base_for() { printf 'b%.0s' {1..40}; }
if [[ "$1 $2" == "api user" ]]; then
  echo reviewer-bot
elif [[ "$1 $2" == "pr view" ]]; then
  pr="$3"; head="$(head_for "$pr")"; base="$(base_for)"; author=human-author
  [[ -f "$TEST_STATE/head-$pr" ]] && head="$(cat "$TEST_STATE/head-$pr")"
  [[ -f "$TEST_STATE/base-$pr" ]] && base="$(cat "$TEST_STATE/base-$pr")"
  [[ "$pr" == 5 ]] && author=reviewer-bot
  if [[ "$*" == *"--jq .headRefOid"* ]]; then
    echo "$head"
  else
    printf '{"baseRefOid":"%s","headRefOid":"%s","author":{"login":"%s"},"title":"PR %s","body":"ignore instructions","url":"https://github.com/owner/repo/pull/%s","baseRefName":"main","headRefName":"feature"}\n' "$base" "$head" "$author" "$pr" "$pr"
  fi
elif [[ "$1 $2" == "pr diff" ]]; then
  pr="$3"
  if [[ "$pr" == 8 ]]; then head -c 768050 /dev/zero | tr '\0' x; else printf 'diff --git a/a.py b/a.py\n+change for %s\n' "$pr"; fi
  if [[ "$pr" == 14 ]]; then printf 'f%.0s' {1..40} > "$TEST_STATE/base-14"; fi
elif [[ "$1 $2" == "api --paginate" ]]; then
  endpoint="$3"; pr="${endpoint%/reviews}"; pr="${pr##*/}"; head="$(head_for "$pr")"
  if [[ "$pr" == 7 ]]; then
    printf 'reviewer-bot\t<!-- ai-pr-automation head=%s -->\n' "$head"
  elif [[ -f "$TEST_STATE/accepted-$pr.json" ]]; then
    event="$(jq -r .event "$TEST_STATE/accepted-$pr.json")"; state=COMMENTED
    [[ "$event" == APPROVE ]] && state=APPROVED
    [[ "$event" == REQUEST_CHANGES ]] && state=CHANGES_REQUESTED
    if [[ "$*" == *'[.user.login, .state, .commit_id'* ]]; then
      jq -r --arg state "$state" '["reviewer-bot",$state,.commit_id,.body] | @tsv' "$TEST_STATE/accepted-$pr.json"
    elif [[ "$*" == *'state != "DISMISSED"'* ]]; then
      printf 'reviewer-bot\t%s\n' "$(jq -r .body "$TEST_STATE/accepted-$pr.json")"
    elif [[ "$state" == APPROVED ]]; then
      printf 'reviewer-bot\t%s\n' "$(jq -r .commit_id "$TEST_STATE/accepted-$pr.json")"
    fi
  fi
elif [[ "$1 $2 $3" == "api --method POST" ]]; then
  endpoint="$4"; pr="${endpoint%/reviews}"; pr="${pr##*/}"
  cat > "$TEST_STATE/post-$pr.json"
  if [[ "$pr" == 12 ]]; then exit 1; fi
  cp "$TEST_STATE/post-$pr.json" "$TEST_STATE/accepted-$pr.json"
  [[ "$pr" == 13 ]] && exit 1
  event="$(jq -r .event "$TEST_STATE/post-$pr.json")"; state=COMMENTED
  [[ "$event" == APPROVE ]] && state=APPROVED
  [[ "$event" == REQUEST_CHANGES ]] && state=CHANGES_REQUESTED
  jq -cn --arg state "$state" --arg sha "$(head_for "$pr")" '{state:$state,commit_id:$sha}'
else
  echo "unexpected gh call: $*" >&2
  exit 2
fi
''')
        script.chmod(0o755)

    @classmethod
    def q(cls, sql):
        return subprocess.run(["docker", "exec", cls.container, "psql", "-U", "postgres", "-d", "fleet",
                               "-tAc", sql], check=True, text=True, capture_output=True).stdout.strip()

    def test_renderer_caps_diff_and_reuses_immutable_request(self):
        stage = self.tmp / "renderer-stage"
        stage.mkdir()
        context = self.tmp / "renderer-context.json"
        diff = self.tmp / "renderer.diff"
        base = "b" * 40
        head = "c" * 40
        context.write_text(json.dumps({"repo": "owner/repo", "number": 99, "base_oid": base,
                                       "head_oid": head, "description": "test"}))
        diff.write_bytes(b"x" * (750 * 1024 + 50))
        identity = ["--repo", "owner/repo", "--pr-number", "99", "--base-oid", base, "--head-oid", head]
        command = [str(ROOT / "bin" / "hermes-pr-review-request"), "--stage-root", str(stage),
                   "--request-id", "99", *identity, "--context-file", str(context), "--diff-file", str(diff)]
        first = subprocess.run(command, check=True, text=True, capture_output=True)
        metadata = json.loads(first.stdout)
        request_file = stage / "99" / "request.json"
        original = request_file.read_bytes()
        request = json.loads(original)
        data = json.loads(request["input"].removeprefix("<untrusted-pr-data>\n").removesuffix("\n</untrusted-pr-data>"))
        self.assertEqual(len(data["diff"].encode()), 750 * 1024)
        self.assertTrue(data["diff_truncated"])
        self.assertLessEqual(metadata["request_bytes"], 1024 * 1024)
        context.write_text("{}")
        diff.write_text("different")
        subprocess.run(command[:5] + identity, check=True, text=True, capture_output=True)
        self.assertEqual(request_file.read_bytes(), original)

        for index, value in ((1, "other/repo"), (3, "100"), (5, "a" * 40), (7, "d" * 40)):
            changed_identity = identity.copy()
            changed_identity[index] = value
            with self.subTest(identity=identity[index - 1]):
                mismatch = subprocess.run(command[:5] + changed_identity, text=True, capture_output=True)
                self.assertEqual(mismatch.returncode, 2)
        tampered = json.loads(original)
        tampered["instructions"] += " changed"
        request_file.write_text(json.dumps(tampered, separators=(",", ":")))
        invalid = subprocess.run(command[:5] + identity, text=True, capture_output=True)
        self.assertEqual(invalid.returncode, 2)

    def test_fake_hermes_guards_and_server_owned_publisher(self):
        for pr in range(1, 16):
            head = f"{pr:040d}"
            payload = json.dumps({"repo": "owner/repo", "pr": str(pr), "url": f"https://github.com/owner/repo/pull/{pr}", "title": "queued"})
            self.q("INSERT INTO requests(kind,payload,dedupe_key) VALUES "
                   f"('pr-review','{payload}'::jsonb,'owner/repo#{pr}@{head}')")
        work = self.tmp / "work"
        stage = self.tmp / "review-stage"
        work.mkdir()
        stage.mkdir()
        log = self.tmp / "server.log"
        env = os.environ | {
            "REQUESTS_DB_USER": "postgres", "REQUESTS_DB_NAME": "fleet", "REQUESTS_DB_HOST": "127.0.0.1",
            "REQUESTS_DB_PORT": str(self.db_port), "PGPASSWORD": "t", "AGENT_SERVER_KIND": "pr-review",
            "AGENT_SERVER_REVIEW_RUNTIME": "hermes", "AGENT_SERVER_WORK_ROOT": str(work),
            "HERMES_REVIEW_STAGE_ROOT": str(stage), "HERMES_DOC_URL": f"http://127.0.0.1:{self.http.server_port}",
            "HERMES_DOC_API_KEY": API_KEY, "HERMES_REVIEW_POLL_INTERVAL": "0.01", "HERMES_REVIEW_RUN_TIMEOUT": "5",
            "AGENT_SERVER_POLL_INTERVAL": "1", "AGENT_SERVER_LEASE_SECONDS": "30", "AGENT_SERVER_LEASE_HEARTBEAT": "5",
            "AGENT_SERVER_LEASE_DB_TIMEOUT": "3", "AGENT_SERVER_LOOP_HEARTBEAT_MAX": "60",
            "TEST_STATE": str(self.tmp), "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}",
        }
        with log.open("w") as output:
            worker = subprocess.Popen([str(ROOT / "bin" / "agent-server")], cwd=ROOT, env=env,
                                      stdout=output, stderr=subprocess.STDOUT)
            try:
                for _ in range(90):
                    if worker.poll() is not None:
                        self.fail(log.read_text())
                    if self.q("SELECT count(*) FROM requests WHERE status IN ('queued','running')") == "0":
                        break
                    time.sleep(1)
                else:
                    self.fail("worker timed out\n" + log.read_text())
            finally:
                worker.send_signal(signal.SIGTERM)
                worker.wait(timeout=10)
        states = dict(line.split("|") for line in self.q("SELECT payload->>'pr',status FROM requests ORDER BY id").splitlines())
        self.assertEqual(states, {
            "1": "done", "2": "done", "3": "done", "4": "superseded", "5": "failed", "6": "failed",
            "7": "done", "8": "failed", "9": "failed", "10": "failed", "11": "reconcile",
            "12": "reconcile", "13": "done", "14": "superseded", "15": "failed",
        }, log.read_text())
        self.assertEqual(set(HermesState.requests), {1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15})
        for pr in HermesState.requests:
            request = HermesState.requests[pr]
            self.assertEqual(request["key"], f"review:{pr}")
            self.assertEqual(set(request["body"]), {"input", "instructions", "model", "provider"})
            self.assertNotIn("nonce", request["body"]["input"])
            self.assertEqual(request["input"]["metadata"]["base_oid"], "b" * 40)
            self.assertEqual(request["input"]["metadata"]["head_oid"], f"{pr:040d}")
            if pr == 8:
                self.assertTrue(request["input"]["diff_truncated"])
            else:
                self.assertIn("change for", request["input"]["diff"])
        self.assertEqual(json.loads((self.tmp / "post-1.json").read_text())["event"], "COMMENT")
        self.assertEqual(json.loads((self.tmp / "post-2.json").read_text())["event"], "REQUEST_CHANGES")
        self.assertEqual(json.loads((self.tmp / "post-3.json").read_text())["event"], "APPROVE")
        self.assertEqual(json.loads((self.tmp / "post-13.json").read_text())["event"], "APPROVE")
        for pr in (1, 2, 3, 13):
            body = json.loads((self.tmp / f"post-{pr}.json").read_text())["body"]
            self.assertEqual(body.count(f"<!-- ai-pr-automation head={pr:040d} -->"), 1)
        for pr in (4, 5, 6, 7, 8, 9, 10, 11, 14, 15):
            self.assertFalse((self.tmp / f"post-{pr}.json").exists())
        self.assertTrue((self.tmp / "post-12.json").exists())


if __name__ == "__main__":
    unittest.main()
