#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("week_calendar", ROOT / "scripts/hermes-week-calendar.py")
calendar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calendar)


class FakeGoogle:
    def __init__(self):
        self.calendars = [{"id": "owner@example.test", "summary": "Primary", "primary": True, "accessRole": "owner"},
                          {"id": "private-id", "summary": "Private", "accessRole": "owner"},
                          {"id": "work-id", "summary": "Work", "accessRole": "reader", "hidden": True}]
        self.items = {"private-id": [], "work-id": [{"id": "fixed", "summary": "Meeting", "start": "2026-10-12T12:00:00-04:00", "end": "2026-10-12T13:00:00-04:00"}], "owner@example.test": []}
        self.inserts = []
        self.patches = []

    def list_calendars(self): return self.calendars
    def list_events(self, calendar_id, _start, _end): return self.items[calendar_id]
    def busy(self, calendar_ids, start, end):
        return [(datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))
                for item_id in calendar_ids for item in self.items[item_id]
                if item["start"] < end.isoformat() and item["end"] > start.isoformat()]
    def get(self, calendar_id, event_id):
        return next((e for e in self.items[calendar_id] if e["id"] == event_id), None)
    def insert(self, calendar_id, event):
        self.inserts.append((calendar_id, event))
        self.items[calendar_id].append(event | {"start": event["start"]["dateTime"], "end": event["end"]["dateTime"], "etag": "v1"})
        return True
    def patch(self, calendar_id, event_id, start, end, etag):
        self.patches.append((calendar_id, event_id, etag))
        item = self.get(calendar_id, event_id)
        item.update(start=start, end=end, etag="v2")


class CalendarGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        boundary = {"account": "owner@example.test", "calendar": "Private", "timezone": "America/New_York"}
        self.boundary = self.root / "week-planner.md"
        self.boundary.write_text("# Private rules\n```json\n" + json.dumps(boundary) + "\n```\n")
        self.boundary.chmod(0o600)
        self.notes = self.root / "week-planner-notes.md"
        self.notes.write_text("# Rules\nSleep first.\n")
        self.notes.chmod(0o600)
        self.google = FakeGoogle()
        self.guard = calendar.CalendarGuard(self.root, self.google, now=datetime(2026, 10, 6, 12, tzinfo=ZoneInfo("America/New_York")))

    def test_dst_duration_uses_elapsed_time(self):
        with self.assertRaisesRegex(ValueError, "duration"):
            self.guard.span("2026-10-31T20:00:00-04:00", "2026-11-01T08:00:00-05:00")

    def test_concurrent_same_key_keeps_one_calendar_block(self):
        def create():
            return self.guard.create("2026-10-12:study", "Study", "2026-10-12T07:00:00-04:00", "2026-10-12T08:00:00-04:00")
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(len(self.google.inserts), 1)
        self.assertEqual({v["status"] for v in values}, {"created", "existing"})

    def test_context_reads_hidden_calendar_and_rules_without_description(self):
        result = self.guard.context("2026-10-12")
        self.assertIn("Sleep first", result["rules"])
        self.assertIn("Meeting", [item["summary"] for item in result["events"]])
        self.assertEqual({item["calendar"] for item in result["events"]}, {"Work"})
        self.assertFalse(self.google.inserts)

    def test_create_only_target_idempotent_private_and_conflict_guard(self):
        with self.assertRaisesRegex(ValueError, "busy"):
            self.guard.create("2026-10-12:lunch", "Lunch", "2026-10-12T12:00:00-04:00", "2026-10-12T12:30:00-04:00")
        self.assertFalse(self.google.inserts)
        created = self.guard.create("2026-10-12:workout", "Workout", "2026-10-12T06:00:00-04:00", "2026-10-12T06:45:00-04:00")
        self.assertEqual(created["status"], "created")
        self.assertEqual(self.google.inserts[0][0], "private-id")
        self.assertEqual(self.google.inserts[0][1]["visibility"], "private")
        self.assertNotIn("attendees", self.google.inserts[0][1])
        self.assertEqual(created["id"], self.guard.create("2026-10-12:workout", "Workout", "2026-10-12T07:00:00-04:00", "2026-10-12T07:45:00-04:00")["id"])
        self.assertEqual(len(self.google.inserts), 1)
        self.google.items["private-id"][0]["status"] = "cancelled"
        self.assertEqual(self.guard.create("2026-10-12:workout", "Workout", "2026-10-12T08:00:00-04:00", "2026-10-12T08:45:00-04:00")["status"], "deleted_by_user")
        self.assertEqual(len(self.google.inserts), 1)

    def test_move_rejects_unowned_or_conflicting_and_appends_rules_privately(self):
        with self.assertRaisesRegex(ValueError, "owned"):
            self.guard.move("fixed", "2026-10-12T14:00:00-04:00", "2026-10-12T14:30:00-04:00")
        created = self.guard.create("2026-10-12:reading", "Reading", "2026-10-12T21:00:00-04:00", "2026-10-12T21:30:00-04:00")
        with self.assertRaisesRegex(ValueError, "busy"):
            self.guard.move(created["id"], "2026-10-12T12:00:00-04:00", "2026-10-12T12:30:00-04:00")
        self.guard.move(created["id"], "2026-10-12T20:00:00-04:00", "2026-10-12T20:30:00-04:00")
        self.assertEqual(self.google.patches[-1][0], "private-id")
        self.guard.append_rule("From now on, Sunday meal prep takes two hours.")
        self.assertIn("From now on", self.notes.read_text())
        self.assertEqual(self.notes.stat().st_mode & 0o777, 0o600)

    def test_owner_only_rule_file_and_google_error_paths(self):
        self.notes.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            self.guard.context("2026-10-12")
        self.notes.chmod(0o600)
        adapter = calendar.GoogleCalendar.__new__(calendar.GoogleCalendar)
        adapter.api = mock.Mock()
        adapter.api.freebusy.return_value.query.return_value.execute.return_value = {"calendars": {"work-id": {"errors": [{"reason": "forbidden"}]}}}
        start = datetime(2026, 10, 12, 8, tzinfo=ZoneInfo("America/New_York"))
        with self.assertRaisesRegex(ValueError, "availability"):
            adapter.busy(["work-id"], start, start.replace(hour=9))
        request = adapter.api.events.return_value.patch.return_value
        request.headers = {}
        adapter.patch("private-id", "pl012345", start.isoformat(), start.replace(hour=9).isoformat(), "etag-v1")
        self.assertEqual(request.headers["If-Match"], "etag-v1")
        adapter.api.events.return_value.patch.assert_called_with(calendarId="private-id", eventId="pl012345",
            body={"start": {"dateTime": start.isoformat()}, "end": {"dateTime": start.replace(hour=9).isoformat()}}, sendUpdates="none")

    def test_profile_disables_unrestricted_tools(self):
        import yaml
        profile = ROOT / "agent-config/hermes/profiles/week-planner"
        config = yaml.safe_load((profile / "config.yaml").read_text())
        self.assertEqual(config["model"]["provider"], "openai-codex")
        self.assertTrue({"terminal", "file", "code_execution", "browser", "computer_use", "skills", "memory"}.issubset(
            set(config["agent"]["disabled_toolsets"])))
        self.assertEqual(set(config["mcp_servers"]), {"week-calendar"})
        self.assertIn("HERMES_WEEK_CALENDAR_SCRIPT", config["mcp_servers"]["week-calendar"]["args"][1])
        self.assertIn("HERMES_WEEK_PLANNER_HOME", config["mcp_servers"]["week-calendar"]["env"]["HERMES_HOME"])

    def test_wrong_account_and_ambiguous_calendar_fail_closed(self):
        self.google.calendars[0]["id"] = "other@example.test"
        with self.assertRaisesRegex(ValueError, "account"):
            self.guard.context("2026-10-12")
        self.google.calendars[0]["id"] = "owner@example.test"
        self.google.calendars.append({"id": "another", "summary": "Private", "accessRole": "owner"})
        with self.assertRaisesRegex(ValueError, "calendar"):
            self.guard.create("2026-10-12:test", "Test", "2026-10-12T08:00:00-04:00", "2026-10-12T08:30:00-04:00")
        self.assertFalse(self.google.inserts)


if __name__ == "__main__": unittest.main()
