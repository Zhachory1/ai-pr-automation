#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/hermes-queue-client.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("hermes_queue_client", SCRIPT)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class QueueClientTest(unittest.TestCase):
    def setUp(self):
        self.value = {"version": 1, "kind": "prd-write", "title": "Write PRD",
                      "requirements": "Explain the change", "repositories": ["Owner/Repo"]}
        self.home = pathlib.Path("/host/.hermes")
        self.binary = pathlib.Path("/host/.local/bin/hermes")
        self.env = {"HOME": "/host", "HERMES_HOME": str(self.home)}

    def test_refuses_untrusted_fields_and_oversized_input(self):
        for value in ({**self.value, "model": "other"}, {**self.value, "kind": "unknown"},
                      {**self.value, "repositories": ["../private"]},
                      {**self.value, "requirements": "x" * 2049}):
            with self.subTest(value=value.get("kind")), self.assertRaises(ValueError):
                client.intake(client.canonical(value))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            client.intake(b'{"version":1,"version":1}')
        with self.assertRaisesRegex(ValueError, "8 KiB"):
            client.intake(b"x" * 8193)

    def test_document_intake_rejects_blank_title_or_requirements(self):
        for change in ({"title": "  \n "}, {"requirements": " \t "}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "invalid title or requirements"):
                client.intake(client.canonical({**self.value, **change}))

    def test_all_six_request_kinds_are_typed(self):
        for kind in ("pr-review", "pr-safety"):
            value = {"version": 1, "kind": kind, "repository": "Owner/Repo", "pr": 7, "head_sha": "a" * 40}
            self.assertEqual(client.intake(client.canonical(value)), value)
            with self.assertRaises(ValueError): client.intake(client.canonical({**value, "profile": "root"}))
        maintain = {"version": 1, "kind": "pr-maintain", "repository": "Owner/Repo", "pr": 7}
        self.assertEqual(client.intake(client.canonical(maintain)), maintain)
        with self.assertRaises(ValueError): client.intake(client.canonical({**maintain, "round": 1}))
        for kind in ("prd-write", "dd-write", "roadmap-write"):
            self.assertEqual(client.intake(client.canonical({**self.value, "kind": kind}))["kind"], kind)

    def test_host_uses_current_account_and_refuses_privilege_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            (home / ".hermes").mkdir()
            binary = home / ".local/bin/hermes"
            binary.parent.mkdir(parents=True)
            binary.touch()
            with patch.object(client.os, "getuid", return_value=98765), \
                 patch.object(client.os, "geteuid", return_value=98765), \
                 patch.object(client.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(home))):
                self.assertEqual(client.host()[:3], (home, home / ".hermes", binary))
            with patch.object(client.os, "getuid", return_value=98765), \
                 patch.object(client.os, "geteuid", return_value=0):
                with self.assertRaisesRegex(ValueError, "do not use sudo"):
                    client.host()
            with patch.object(client.os, "getuid", return_value=0), \
                 patch.object(client.os, "geteuid", return_value=0):
                with self.assertRaisesRegex(ValueError, "non-root"):
                    client.host()

    def test_document_request_uses_existing_enqueue_and_reads_writer(self):
        calls = []
        operation = "prd-" + client.hashlib.sha256(client.canonical({
            "title": "Write PRD", "requester": "local-agent", "requirements": "Explain the change",
            "repositories": ["Owner/Repo"]})).hexdigest()

        def fake_run(command, env, input_data=None):
            calls.append((command, input_data))
            if "hermes-prd-kanban-enqueue.py" in str(command):
                self.assertEqual(json.loads(input_data)["operation_id"], operation)
                return {"board": "prd-write", "operation_id": operation, "tasks": {"writer": "t_12345678"}}
            return [{"id": "t_12345678", "tenant": operation, "status": "ready",
                     "body": client.canonical({"workflow": "prd-write", "operation": operation,
                                               "stage": "writer", "round": 0}).decode()}]

        with patch.object(client.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as authority, \
             patch.object(client, "run", side_effect=fake_run):
            result = client.request(self.value, self.home, self.binary, self.env)
        self.assertEqual((result["operation_id"], result["task_id"], result["status"]),
                         (operation, "t_12345678", "ready"))
        self.assertEqual(authority.call_count, 1)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(str(self.binary) in command or "hermes-prd-kanban-enqueue.py" in str(command) for command, _ in calls))

    def test_pr_review_uses_runs_api_and_exact_head(self):
        value = {"kind": "pr-review", "repository": "Owner/Repo", "pr": 7, "head_sha": "a" * 40}
        operation = client.identity("pr-review", "Owner/Repo", 7, value["head_sha"])["operation_id"]
        info = {"number": 7, "title": "Review", "url": "https://github.com/Owner/Repo/pull/7",
                "headRefOid": value["head_sha"], "state": "OPEN"}
        admitted = {"operation_id": operation, "run_id": "run-1", "status": "started", "replayed": False}
        with patch.object(client, "authorized"), patch.object(client, "github", return_value=info), \
             patch.object(client, "profile_key", return_value="review-key"), \
             patch.object(client.run_api, "submit", return_value=admitted) as submit:
            result = client.pr_request(value, self.home, self.binary, self.env)
            self.assertEqual((result["operation_id"], result["run_id"], result["kind"]),
                             (operation, "run-1", "pr-review"))
            submit.assert_called_once_with("pr-review", "Owner/Repo", 7, value["head_sha"], "review-key",
                                           feedback_digest=None)
            with self.assertRaisesRegex(ValueError, "head changed"):
                client.pr_request({**value, "head_sha": "b" * 40}, self.home, self.binary, self.env)

    def test_pr_maintenance_feedback_is_host_computed(self):
        review = {"id": 10, "user": {"login": "reviewer"}, "body": "fix this"}
        def fake_github(args, env, allow_failure=False):
            if args[:2] == ["api", "user"]: return {"login": "fleet"}
            if "reviews?" in str(args): return [review]
            if "graphql" in args:
                return {"data": {"repository": {"pullRequest": {"reviewThreads": {
                    "nodes": [], "pageInfo": {"hasNextPage": False}}}}}}
            return []
        with patch.object(client, "github", side_effect=fake_github):
            digest = client.feedback("Owner/Repo", 7, self.env)
        expected = client.hashlib.sha256(client.canonical([["review", "10", client.hashlib.sha256(b"fix this").hexdigest()]])).hexdigest()
        self.assertEqual(digest, expected)

    def test_maintenance_request_uses_feedback_bound_runs_api(self):
        value = {"kind": "pr-maintain", "repository": "Owner/Repo", "pr": 7}
        head, digest = "a" * 40, "b" * 64
        operation = client.identity("pr-maintain", "Owner/Repo", 7, head, digest)["operation_id"]
        info = {"number": 7, "title": "Maintain", "url": "https://github.com/Owner/Repo/pull/7",
                "headRefOid": head, "state": "OPEN"}
        with patch.object(client, "authorized"), patch.object(client, "github", return_value=info), \
             patch.object(client, "feedback", return_value=digest), \
             patch.object(client, "profile_key", return_value="maintain-key"), \
             patch.object(client.run_api, "submit", return_value={"operation_id": operation,
                 "run_id": "run-2", "status": "started", "replayed": False}) as submit:
            result = client.pr_request(value, self.home, self.binary, self.env)
        self.assertEqual((result["operation_id"], result["run_id"]), (operation, "run-2"))
        submit.assert_called_once_with("pr-maintain", "Owner/Repo", 7, head, "maintain-key",
                                       feedback_digest=digest)

    def test_safety_request_uses_existing_snapshot_producer(self):
        value = {"kind": "pr-safety", "repository": "Owner/Repo", "pr": 7, "head_sha": "a" * 40}
        policy = {key: "configured" for key in ("PR_SAFETY_MERGED_PR_AUTHORS", "PR_SAFETY_SNAPSHOT_ROOT",
                  "PR_SAFETY_POLICY_ROOT", "PR_SAFETY_POLICY_PATH", "PR_SAFETY_POLICY_VERSION", "PR_SAFETY_POLICY_DIGEST")}
        policy["PR_SAFETY_MERGED_PR_AUTHORS"] = "fleet"
        info = {"state": "closed", "merged_at": "2026-09-30", "merge_commit_sha": value["head_sha"],
                "base": {"sha": "b" * 40}, "user": {"login": "fleet"}}
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            producer = root / "hermes-pr-safety-producer"
            producer.touch()
            def fake_producer(command, **kwargs):
                self.assertEqual(command, [str(producer)])
                record = pathlib.Path(kwargs["env"]["PR_SAFETY_MERGED_PR_INPUT_FILE"])
                self.assertEqual(json.loads(record.read_text())["mergeSha"], value["head_sha"])
                return SimpleNamespace(returncode=0)
            with patch.object(client, "ROOT", root), patch.dict(client.os.environ, policy), \
                 patch.object(client, "authorized"), patch.object(client, "github", return_value=info), \
                 patch.object(client.subprocess, "run", side_effect=fake_producer), \
                 patch.object(client, "status", return_value={"board": "pr-safety-council", "status": "blocked"}):
                self.assertEqual(client.safety_request(value, self.binary, self.env)["status"], "blocked")

    def test_status_check_selects_latest_document_task_on_each_board(self):
        for prefix, board in (("prd", "prd-write"), ("design", "design-write"), ("roadmap", "roadmap-write")):
            operation = prefix + "-" + "a" * 64
            writer = {"id": "t_00000001", "tenant": operation, "status": "done",
                      "body": client.canonical({"workflow": board, "operation": operation,
                                                "stage": "writer", "round": 0}).decode()}
            synthesis = {"id": "t_00000002", "tenant": operation, "status": "blocked",
                         "body": client.canonical({"workflow": board, "operation": operation,
                                                   "stage": "synthesis", "round": 0}).decode()}
            with self.subTest(board=board), patch.object(client, "run", return_value=[writer, synthesis]):
                result = client.status(operation, self.binary, self.env)
                self.assertEqual((result["board"], result["task_id"], result["status"]),
                                 (board, synthesis["id"], "blocked"))

    def test_status_check_selects_safety_synthesis(self):
        operation = "pr-safety-" + "a" * 64
        nonce = client.hashlib.sha256(f"direct:{operation}".encode()).hexdigest()[:32]
        workflow = "pr-risk-council-" + client.hashlib.sha256(f"{operation}:{nonce}".encode()).hexdigest()[:32]
        tasks = [{"id": f"t_{index:08x}", "tenant": workflow, "status": "ready",
                  "body": client.canonical({"workflow_id": workflow, "role": role}).decode()}
                 for index, role in enumerate(("review", "security", "reliability", "architecture", "synthesis"), 1)]
        with patch.object(client, "run", return_value=tasks):
            result = client.status(operation, self.binary, self.env)
        self.assertEqual((result["board"], result["task_id"]), ("pr-safety-council", "t_00000005"))

    def test_profile_key_reads_installed_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            profile = home / "profiles/pr-review-v1"
            profile.mkdir(parents=True)
            (profile / ".env").write_text("API_SERVER_KEY=review-key\n")
            self.assertEqual(client.profile_key(home, "pr-review"), "review-key")
            with self.assertRaisesRegex(ValueError, "unavailable"):
                client.profile_key(home, "pr-maintain")

    def test_pr_client_request_and_run_id_status(self):
        head = "a" * 40
        value = {"version": 1, "kind": "pr-review", "repository": "Owner/Repo", "pr": 7,
                 "head_sha": head}
        operation = client.identity("pr-review", "Owner/Repo", 7, head)["operation_id"]
        info = {"number": 7, "title": "Review", "url": "https://github.com/Owner/Repo/pull/7",
                "headRefOid": head, "state": "OPEN"}
        admitted = {"operation_id": operation, "run_id": "run-1", "status": "started", "replayed": False}
        with patch.object(client.sys, "argv", ["hermes-queue-client.py", "request"]), \
             patch.object(client.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(client.canonical(value)))), \
             patch.object(client, "host", return_value=(None, self.home, self.binary, self.env)), \
             patch.object(client, "authorized"), patch.object(client, "github", return_value=info), \
             patch.object(client, "profile_key", return_value="review-key"), \
             patch.object(client.run_api, "submit", return_value=admitted) as submit, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            client.main()
        self.assertEqual(json.loads(output.getvalue())["operation_id"], operation)
        self.assertEqual(submit.call_args.args[:4], ("pr-review", "Owner/Repo", 7, head))
        with patch.object(client.sys, "argv", ["hermes-queue-client.py", "status-check", "pr-review", "run-1"]), \
             patch.object(client, "host", return_value=(None, self.home, self.binary, self.env)), \
             patch.object(client, "profile_key", return_value="review-key"), \
             patch.object(client.run_api, "status", return_value={"run_id": "run-1", "status": "completed"}) as status, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            client.main()
        self.assertEqual(json.loads(output.getvalue())["run_id"], "run-1")
        status.assert_called_once_with("pr-review", "run-1", "review-key")

    def test_status_check_refuses_wrong_board(self):
        operation = "prd-" + "a" * 64
        with patch.object(client.sys, "argv", ["hermes-queue-client.py", "status-check", "dd-write", operation]), \
             patch.object(client, "host", return_value=(None, self.home, self.binary, self.env)), \
             patch.object(client, "status", return_value={"kind": "prd-write", "operation_id": operation}), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "does not belong"):
                client.main()

    def test_missing_repo_grant_does_not_enqueue(self):
        with patch.object(client.subprocess, "run", return_value=SimpleNamespace(returncode=3)), patch.object(client, "run") as run:
            with self.assertRaisesRegex(ValueError, "not granted"):
                client.request(self.value, self.home, self.binary, self.env)
            run.assert_not_called()

    def test_roadmap_requires_existing_board(self):
        value = {**self.value, "kind": "roadmap-write"}
        with patch.object(client.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             patch.object(client, "run", return_value=[]) as run:
            with self.assertRaisesRegex(ValueError, "not activated"):
                client.request(value, self.home, self.binary, self.env)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__": unittest.main()
