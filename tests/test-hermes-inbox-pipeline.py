#!/usr/bin/env python3
from datetime import date, datetime, timezone
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_pipeline as pipeline
import hermes_inbox_reader as reader


class FakeGmail:
    def __init__(self, now):
        self.when = str(reader.previous_day(now)[1])
        self.reads = 0

    def profile(self):
        return {"emailAddress": "zhachory1@gmail.com"}

    def list_messages(self, query, *, label_ids, page_token=None):
        self.reads += 1
        return {"messages": [{"id": "first"}, {"id": "second"}]}

    def get_message(self, message_id, *, format, metadata_headers):
        return {"id": message_id, "threadId": "thread-" + message_id,
                "internalDate": self.when, "labelIds": ["INBOX"], "snippet": "synthetic", "payload": {"headers": []}}


class PipelineTest(unittest.TestCase):
    def test_deadline_stops_before_model_and_marks_day_failed(self):
        now=datetime(2026,10,8,12,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'manifest.sqlite'
            with (mock.patch.object(pipeline.luna,'classify') as classify,
                  mock.patch.object(pipeline.daily,'kanban_notice')):
                with self.assertRaises(TimeoutError):
                    pipeline.run_once(path,FakeGmail(now),'/fake/hermes','zhachory1@gmail.com',
                                      date(2026,10,7),now,deadline=0)
                classify.assert_not_called()

    def test_fake_luna_then_kanban_one_card_one_skip_no_repeat(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        gmail = FakeGmail(now)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            admitted = []
            def classify(_binary, _workdir, _day, message):
                return "job" if message["id"] == "first" else "skip"
            def admit(_binary, _account, classifier, day, message, key):
                route = classifier(message)
                if route == "skip":
                    return "skipped"
                admitted.append((day, message["id"], key, route))
                return "admitted"
            with (mock.patch.object(pipeline.luna, "classify", side_effect=classify),
                  mock.patch.object(pipeline.intake, "admit", side_effect=admit),
                  mock.patch.object(pipeline.daily, "kanban_notice")):
                args = (root / "manifest.sqlite", gmail, "/fake/hermes", "zhachory1@gmail.com",
                        date(2026, 10, 7), now)
                self.assertEqual(pipeline.run_once(*args), 2)
                self.assertEqual(pipeline.run_once(*args), 0)
            self.assertEqual(len(admitted), 1)
            self.assertEqual(admitted[0][1], "first")
            self.assertEqual(gmail.reads, 1)


if __name__ == "__main__":
    unittest.main()
