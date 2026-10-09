#!/usr/bin/env python3
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_sol as sol


THREAD = [{"id": "synthetic-id", "internalDate": "1760000000000", "text": "Could we talk about mentorship?",
           "headers": [{"name": "From", "value": "person@example.test"},
                       {"name": "Subject", "value": "Synthetic question"}]}]


class SolRunTest(unittest.TestCase):
    def test_synthetic_thread_and_bounded_voice_only(self):
        with tempfile.TemporaryDirectory() as root:
            with (mock.patch.object(sol.subprocess, "run") as command,
                  mock.patch.object(sol, "_profile_home", return_value=pathlib.Path("/private/profiles/inbox-sol")),
                  mock.patch.object(sol, "_checked_binary", return_value="/fake/hermes")):
                command.return_value.returncode = 0
                command.return_value.stdout = ('{"type":"system","subtype":"init"}\n'
                    '{"type":"result","exit_code":0,"text":"{\\"body\\":\\"Happy to help. What questions do you have?\\"}"}\n')
                text = sol.draft("/fake/hermes", pathlib.Path(root), "help", THREAD,
                                 "Synthetic voice: concise and kind.")
            self.assertIn("What questions", text)
            args = command.call_args.args[0]
            self.assertEqual(args[args.index("--format") + 1], "stream-json")
            self.assertEqual(command.call_args.kwargs["env"]["HERMES_HOME"], "/private/profiles/inbox-sol")
            payload = json.loads(command.call_args.kwargs["input"])
            self.assertEqual(payload["route"], "help")
            self.assertEqual(payload["thread"][0]["sender"], "person@example.test")
            self.assertNotIn("synthetic-id", command.call_args.kwargs["input"])
            self.assertNotIn("Approve", command.call_args.kwargs["input"])

    def test_private_persona_source_is_bounded_and_not_a_symlink(self):
        with tempfile.TemporaryDirectory() as home:
            path = pathlib.Path(home) / "private-docs/inbox/zhachory_volker_persona.md"
            path.parent.mkdir(parents=True)
            path.write_text("Synthetic private voice")
            with mock.patch.object(sol.Path, "home", return_value=pathlib.Path(home)):
                self.assertEqual(sol.read_voice(), "Synthetic private voice")
                path.write_text("x" * 8001)
                with self.assertRaises(ValueError):
                    sol.read_voice()
                path.unlink()
                path.symlink_to(pathlib.Path(home) / "other")
                with self.assertRaises(ValueError):
                    sol.read_voice()

    def test_missing_context_rejected_before_provider(self):
        with mock.patch.object(sol.subprocess, "run") as command:
            with self.assertRaises(ValueError):
                sol.draft("/fake/hermes", pathlib.Path("/tmp"), "help", THREAD, "")
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
