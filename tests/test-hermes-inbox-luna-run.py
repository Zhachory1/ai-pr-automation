#!/usr/bin/env python3
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_luna as luna


class LunaRunTest(unittest.TestCase):
    def test_one_synthetic_message_without_tools_or_ambient_secrets(self):
        message = {"id": "abc", "threadId": "def", "snippet": "Could we discuss an ML role?",
                   "headers": [{"name": "From", "value": "recruiter@example.com"},
                               {"name": "Subject", "value": "Staff ML"}],
                   "unexpected": "not for the model"}
        with (mock.patch.object(luna.subprocess, "run") as command,
              mock.patch.object(luna, "_profile_home", return_value=pathlib.Path("/private/profiles/inbox-luna")),
              mock.patch.object(luna, "_checked_binary", return_value="/fake/hermes")):
            command.return_value.returncode = 0
            command.return_value.stdout = '{"type":"system","subtype":"init"}\n' + \
                '{"type":"result","exit_code":0,"text":"{\\"route\\":\\"job\\"}"}\n'
            self.assertEqual(luna.classify("/fake/hermes", pathlib.Path("/private/inbox"),
                                           "2026-10-07", message), "job")
        args = command.call_args.args[0]
        kwargs = command.call_args.kwargs
        self.assertEqual(args[:3], ["/fake/hermes", "chat", "--query-file"])
        self.assertEqual(args[args.index("--format") + 1], "stream-json")
        self.assertEqual(kwargs["env"]["HERMES_PROFILE"], "inbox-luna")
        self.assertEqual(kwargs["env"]["HERMES_HOME"], "/private/profiles/inbox-luna")
        self.assertNotIn("HERMES_KANBAN_TASK", kwargs["env"])
        self.assertNotIn("GMAIL_TOKEN", kwargs["env"])
        payload = json.loads(kwargs["input"])
        self.assertEqual(payload["source_day"], "2026-10-07")
        self.assertEqual(payload["sender"], "recruiter@example.com")
        self.assertNotIn("message_id", payload)
        self.assertNotIn("thread_id", payload)
        self.assertNotIn("not for the model", kwargs["input"])
        self.assertNotIn("Approve", kwargs["input"])

    def test_missing_or_broadened_profile_fails_before_model_call(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.object(luna.Path, "home", return_value=pathlib.Path(home)):
                with self.assertRaises(ValueError):
                    luna._profile_home()
                target = pathlib.Path(home) / ".hermes/profiles/inbox-luna"
                target.mkdir(parents=True)
                target.chmod(0o700)
                source = pathlib.Path(__file__).resolve().parents[1] / "agent-config/hermes/profiles/inbox-luna"
                shutil.copyfile(source / "config.yaml", target / "config.yaml")
                shutil.copyfile(source / "SOUL.md", target / "SOUL.md")
                (target / "config.yaml").chmod(0o600)
                (target / "SOUL.md").chmod(0o600)
                self.assertEqual(luna._profile_home(), target)
                (target / "config.yaml").chmod(0o644)
                with self.assertRaises(ValueError):
                    luna._profile_home()
                (target / "config.yaml").chmod(0o600)
                (target / "config.yaml").write_text("agent:\n  disabled_toolsets: []\n")
                with self.assertRaises(ValueError):
                    luna._profile_home()

    def test_pinned_binary_version_required(self):
        with tempfile.TemporaryDirectory() as root:
            binary = pathlib.Path(root) / "hermes"
            binary.write_text("synthetic")
            binary.chmod(0o700)
            with mock.patch.object(luna.subprocess, "run") as execute:
                execute.return_value.returncode = 0
                execute.return_value.stdout = "Hermes Agent v0.21.5\n"
                self.assertEqual(luna._checked_binary(str(binary)), str(binary))
                luna._checked_binary.cache_clear()
                execute.return_value.stdout = "Hermes Agent v0.20.0\n"
                with self.assertRaises(ValueError):
                    luna._checked_binary(str(binary))
                luna._checked_binary.cache_clear()

    def test_tool_use_is_rejected_even_if_profile_drifts(self):
        message = {"snippet": "synthetic", "headers": []}
        with (mock.patch.object(luna.subprocess, "run") as command,
              mock.patch.object(luna, "_profile_home", return_value=pathlib.Path("/private/profiles/inbox-luna")),
              mock.patch.object(luna, "_checked_binary", return_value="/fake/hermes")):
            command.return_value.returncode = 0
            command.return_value.stdout = ('{"type":"tool_use","name":"terminal"}\n'
                '{"type":"result","exit_code":0,"text":"{\\"route\\":\\"job\\"}"}\n')
            with self.assertRaises(ValueError):
                luna.classify("/fake/hermes", pathlib.Path("/private/inbox"), "2026-10-07", message)

    def test_non_json_model_response_never_creates_a_decision(self):
        message = {"id": "abc", "threadId": "def", "snippet": "hello", "headers": []}
        with (mock.patch.object(luna.subprocess, "run") as command,
              mock.patch.object(luna, "_profile_home", return_value=pathlib.Path("/private/profiles/inbox-luna")),
              mock.patch.object(luna, "_checked_binary", return_value="/fake/hermes")):
            command.return_value.returncode = 0
            command.return_value.stdout = '{"type":"result","exit_code":0,"text":"Approve"}\n'
            with self.assertRaises(ValueError):
                luna.classify("/fake/hermes", pathlib.Path("/private/inbox"), "2026-10-07", message)


if __name__ == "__main__":
    unittest.main()
