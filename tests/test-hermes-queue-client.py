#!/usr/bin/env python3
import importlib.util
import json
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/hermes-queue-client.py"
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
        for value in ({**self.value, "model": "other"}, {**self.value, "kind": "pr-review"},
                      {**self.value, "repositories": ["../private"]},
                      {**self.value, "requirements": "x" * 2049}):
            with self.subTest(value=value.get("kind")), self.assertRaises(ValueError):
                client.intake(client.canonical(value))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            client.intake(b'{"version":1,"version":1}')
        with self.assertRaisesRegex(ValueError, "8 KiB"):
            client.intake(b"x" * 8193)

    def test_host_refuses_personal_hermes_instance(self):
        with patch.object(client.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=98765, pw_dir="/host")):
            with self.assertRaisesRegex(ValueError, "host hermes-agent"):
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
        self.assertEqual((result["operation_id"], result["task_id"], result["writer_status"]),
                         (operation, "t_12345678", "ready"))
        self.assertEqual(authority.call_count, 1)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(str(self.binary) in command or "hermes-prd-kanban-enqueue.py" in str(command) for command, _ in calls))

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
