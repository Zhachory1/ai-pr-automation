#!/usr/bin/env python3
from datetime import date, datetime, timezone
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_reader as reader


class FakeGmail:
    def __init__(self, pages, messages, account="zhachory1@gmail.com"):
        self.pages, self.messages, self.account = pages, messages, account
        self.queries = []
        self.fetches = []

    def profile(self):
        return {"emailAddress": self.account}

    def list_messages(self, query, *, label_ids, page_token=None):
        self.queries.append((query, label_ids, page_token))
        return self.pages[page_token]

    def get_message(self, message_id, *, format, metadata_headers):
        self.fetches.append((message_id, format, metadata_headers))
        return self.messages[message_id]


class InboxReaderTest(unittest.TestCase):
    def test_new_york_yesterday_dst_boundaries(self):
        for now, expected_hours in ((datetime(2026, 3, 9, 12, tzinfo=timezone.utc), 23),
                                    (datetime(2026, 11, 2, 12, tzinfo=timezone.utc), 25)):
            day, start, end = reader.previous_day(now)
            self.assertEqual((end - start) // 3600000, expected_hours)
            self.assertEqual(day.isoformat(), ("2026-03-08" if expected_hours == 23 else "2026-11-01"))

    def test_previous_day_read_and_unread_inbox_only(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        day, start, end = reader.previous_day(now)
        def message(mid, when, labels):
            return {"id": mid, "threadId": "thread-" + mid, "internalDate": str(when),
                    "labelIds": labels, "snippet": "synthetic"}
        messages = {"read": message("read", start, ["INBOX"]),
                    "unread": message("unread", end - 1, ["INBOX", "UNREAD"]),
                    "archived": message("archived", start + 1, []),
                    "old": message("old", start - 1, ["INBOX"]),
                    "new": message("new", end, ["INBOX"])}
        messages["read"]["payload"] = {"headers": [{"name": "From", "value": "sender@example.com"}],
                                       "body": {"data": "private-body"}}
        messages["read"]["unrelated"] = "must-not-escape"
        api = FakeGmail({None: {"messages": [{"id": mid} for mid in messages], "nextPageToken": "next"},
                         "next": {"messages": [{"id": "read"}]}}, messages)
        result = reader.scan(api, now, "zhachory1@gmail.com", limit=20)
        self.assertEqual([m["id"] for m in result], ["read", "unread"])
        self.assertNotIn("private-body", str(result))
        self.assertNotIn("must-not-escape", str(result))
        self.assertEqual(result[0]["headers"], [{"name": "From", "value": "sender@example.com"}])
        self.assertTrue(all(fmt == "metadata" and headers == ("From", "Reply-To", "Subject")
                            for _, fmt, headers in api.fetches))
        self.assertEqual(api.queries, [(f"after:{start//1000 - 1} before:{end//1000}", ("INBOX",), None),
                                       (f"after:{start//1000 - 1} before:{end//1000}", ("INBOX",), "next")])

    def test_manual_replay_uses_original_new_york_day(self):
        now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        original = date(2026, 10, 7)
        _day, start, end = reader.previous_day(datetime(2026, 10, 8, 12, tzinfo=timezone.utc))
        api = FakeGmail({None: {"messages": [{"id": "m"}]}},
                        {"m": {"id": "m", "threadId": "t", "internalDate": str(start), "labelIds": ["INBOX"]}})
        self.assertEqual([m["id"] for m in reader.scan(api, now, "zhachory1@gmail.com", limit=250, day=original)], ["m"])
        self.assertEqual(api.queries, [(f"after:{start//1000 - 1} before:{end//1000}", ("INBOX",), None)])

    def test_account_mismatch_fails_before_listing(self):
        api = FakeGmail({}, {}, "another@example.com")
        with self.assertRaises(ValueError):
            reader.scan(api, datetime(2026, 10, 8, tzinfo=timezone.utc), "zhachory1@gmail.com", limit=20)
        self.assertEqual(api.queries, [])

    def test_candidate_limit_fails_visibly(self):
        api = FakeGmail({None: {"messages": [{"id": "one"}, {"id": "two"}]}},
                        {"one": {"id": "one", "threadId": "thread-one", "internalDate": "1791417600000", "labelIds": ["INBOX"]}})
        with self.assertRaises(reader.InboxOverflow):
            reader.scan(api, datetime(2026, 10, 8, tzinfo=timezone.utc), "zhachory1@gmail.com", limit=1)


if __name__ == "__main__":
    unittest.main()
