#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("calendar_auth", ROOT / "scripts/hermes-week-calendar-auth.py")
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class CalendarAuthTest(unittest.TestCase):
    def test_only_calendar_scope_and_exact_owned_target(self):
        self.assertEqual(auth.SCOPES, ["https://www.googleapis.com/auth/calendar"])
        self.assertEqual(auth.INSPECT_SCOPES, ["https://www.googleapis.com/auth/calendar.readonly"])
        auth.require_calendar_scope(auth.SCOPES)
        with self.assertRaises(ValueError):
            auth.require_calendar_scope(auth.SCOPES + ["https://www.googleapis.com/auth/gmail.readonly"])
        rules = {"account": "owner@example.test", "calendar": "Private"}
        calendars = [{"id": "owner@example.test", "primary": True, "summary": "Primary", "accessRole": "owner"},
                     {"id": "private-id", "summary": "Private", "accessRole": "owner"}]
        self.assertEqual(auth.verify(calendars, rules), "private-id")
        self.assertEqual([item["name"] for item in auth.owned_choices(calendars, rules)], ["Primary", "Private"])
        with self.assertRaises(ValueError): auth.verify([{**calendars[0], "id": "other@example.test"}, calendars[1]], rules)
        with self.assertRaises(ValueError): auth.verify(calendars + [{"id": "second", "summary": "Private", "accessRole": "owner"}], rules)
        with self.assertRaises(ValueError): auth.verify([calendars[0], {**calendars[1], "accessRole": "reader"}], rules)


if __name__ == "__main__": unittest.main()
