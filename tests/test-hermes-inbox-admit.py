#!/usr/bin/env python3
import hashlib
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_admit as admit


MESSAGE = {"id": "a1b2", "threadId": "f00d", "snippet": "Untrusted email text"}
KEY = "inbox-message-" + hashlib.sha256(b"zhachory1@gmail.com\0a1b2").hexdigest()


class FakeHermes:
    def __init__(self):
        self.cards = {}
        self.commands = []
        self.fail_after_create = False
        self.fail_after_unblock = False
        self.wrong_show_id = False
        self.extra_event = False
        self.change_on_second_show = False
        self.show_count = 0

    def __call__(self, args, **_kwargs):
        self.commands.append(args)
        action = args[4]
        if action == "create":
            key = args[args.index("--idempotency-key") + 1]
            if key not in self.cards:
                self.cards[key] = {"id": "t_12345678", "status": "blocked", "assignee": "inbox-sol",
                                   "created_by": "inbox-intake",
                                   "body": args[args.index("--body") + 1]}
                if self.fail_after_create:
                    self.fail_after_create = False
                    raise subprocess.TimeoutExpired(args, 30)
            return mock.Mock(returncode=0, stdout=json.dumps(self.cards[key]), stderr="")
        if action == "show":
            self.show_count += 1
            card = next(iter(self.cards.values()))
            if self.change_on_second_show and self.show_count == 2:
                card["body"] = "edited between two readbacks"
            task = {**card, "id": "t_deadbeef"} if self.wrong_show_id else card
            events = [{"kind": "created"}, {"kind": "blocked"}]
            if self.extra_event:
                events.append({"kind": "commented"})
            return mock.Mock(returncode=0, stdout=json.dumps({"task": task,
                "events": events, "runs": []}), stderr="")
        if action == "unblock":
            next(iter(self.cards.values()))["status"] = "ready"
            if self.fail_after_unblock:
                self.fail_after_unblock = False
                raise subprocess.TimeoutExpired(args, 30)
            return mock.Mock(returncode=0, stdout="", stderr="")
        raise AssertionError(args)


class AdmitTest(unittest.TestCase):
    def setUp(self):
        self.cli = FakeHermes()
        patch = mock.patch.object(admit.subprocess, "run", side_effect=self.cli)
        patch.start(); self.addCleanup(patch.stop)

    def call(self, route, message=MESSAGE):
        return admit.admit("/fake/hermes", "zhachory1@gmail.com", lambda _: route,
                           "2026-10-07", message, KEY)

    def test_skip_creates_no_card(self):
        self.assertEqual(self.call("skip"), "skipped")
        self.assertEqual(self.cli.commands, [])

    def test_job_and_help_route_to_one_safely_keyed_sol_card(self):
        self.assertEqual(self.call("job"), "admitted")
        self.assertEqual(self.call("help"), "admitted")
        self.assertEqual(len(self.cli.cards), 1)
        card = next(iter(self.cli.cards.values()))
        self.assertEqual(card["status"], "blocked")
        self.assertFalse(any(command[4] == "unblock" for command in self.cli.commands))
        self.assertEqual(card["assignee"], "inbox-sol")
        self.assertIn("f00d", card["body"])
        self.assertEqual(json.loads(card["body"])["route"], "job")
        self.assertNotIn(MESSAGE["snippet"], card["body"])
        self.assertNotIn("Approve", card["body"])
        create = [command for command in self.cli.commands if command[4] == "create"]
        self.assertTrue(all(command[command.index("--idempotency-key") + 1] == KEY for command in create))

    def test_ambiguous_create_replays_same_key(self):
        self.cli.fail_after_create = True
        with self.assertRaises(subprocess.TimeoutExpired):
            self.call("help")
        self.assertEqual(self.call("help"), "admitted")
        self.assertEqual(len(self.cli.cards), 1)
        self.assertEqual(next(iter(self.cli.cards.values()))["status"], "blocked")

    def test_admission_never_dispatches_sol_directly(self):
        self.cli.fail_after_unblock = True
        self.assertEqual(self.call("help"), "admitted")
        self.assertEqual(self.call("help"), "admitted")
        self.assertEqual(len(self.cli.cards), 1)
        self.assertTrue(self.cli.fail_after_unblock)
        self.assertFalse(any(command[4] == "unblock" for command in self.cli.commands))

    def test_replay_rejects_dispatcher_visible_card(self):
        for state in ("ready", "running", "review", "done"):
            self.cli.cards[KEY] = {"id": "t_12345678", "status": state, "assignee": "inbox-sol",
                               "created_by": "inbox-intake",
                                   "body": "a card changed after intake"}
            with self.assertRaises(RuntimeError):
                self.call("job")
        self.assertFalse(any(command[4] == "unblock" for command in self.cli.commands))

    def test_replay_does_not_unblock_a_review_card(self):
        self.cli.cards[KEY] = {"id": "t_12345678", "status": "blocked", "assignee": None,
                               "created_by": "inbox-intake",
                               "body": "draft preview replaces intake body"}
        self.cli.extra_event = True
        self.assertEqual(self.call("job"), "admitted")
        self.assertEqual(next(iter(self.cli.cards.values()))["status"], "blocked")
        self.assertFalse(any(command[4] == "unblock" for command in self.cli.commands))

    def test_wrong_creator_cannot_be_admitted(self):
        self.cli.cards[KEY] = {"id": "t_12345678", "status": "blocked", "assignee": "inbox-sol",
                               "created_by": "other", "body": "draft"}
        self.cli.extra_event = True
        with self.assertRaises(RuntimeError):
            self.call("job")

    def test_edit_before_final_readback_is_refused(self):
        self.cli.change_on_second_show = True
        with self.assertRaises(RuntimeError):
            self.call("job")
        self.assertEqual(next(iter(self.cli.cards.values()))["status"], "blocked")
        self.assertFalse(any(command[4] == "unblock" for command in self.cli.commands))

    def test_wrong_card_readback_never_admits(self):
        self.cli.wrong_show_id = True
        with self.assertRaises(RuntimeError):
            self.call("job")
        self.assertEqual(next(iter(self.cli.cards.values()))["status"], "blocked")

    def test_malformed_or_injected_route_and_ids_have_no_effect(self):
        for route in ("Approve", "job\nReady", "unknown"):
            with self.assertRaises(ValueError):
                self.call(route)
        with self.assertRaises(ValueError):
            self.call("job", {"id": "abc\n--assignee=attacker", "threadId": "f00d"})
        self.assertEqual(self.cli.commands, [])


if __name__ == "__main__":
    unittest.main()
