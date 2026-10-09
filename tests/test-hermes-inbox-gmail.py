#!/usr/bin/env python3
from datetime import datetime, timezone
import base64
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import hermes_inbox_gmail as inbox
import hermes_inbox_reader as reader
import hermes_inbox_luna as luna


class Request:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeService:
    def __init__(self):
        self.calls = []
        self.pages = 0

    def users(self):
        return self

    def messages(self):
        return self

    def getProfile(self, **kwargs):
        self.calls.append(("profile", kwargs))
        return Request({"emailAddress": "zhachory1@gmail.com", "messagesTotal": 999})

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        self.pages += 1
        return Request({"messages": [{"id": "abc"}] if self.pages == 1 else [],
                        "nextPageToken": "next" if self.pages == 1 else None,
                        "resultSizeEstimate": 999})

    def threads(self):
        return self

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return Request({"id": "abc", "threadId": "thread1", "internalDate": "1760000000000",
                        "labelIds": ["INBOX"], "snippet": "synthetic", "payload": {
                            "headers": [{"name": "From", "value": "someone@example.com"}],
                            "body": {"data": "private body"}}, "unexpected": "private body"})


class GmailReadClientTest(unittest.TestCase):
    def test_readonly_api_projection_and_field_masks(self):
        api = FakeService()
        gmail = inbox.GmailReadClient(api)
        self.assertEqual(gmail.profile(), {"emailAddress": "zhachory1@gmail.com"})
        self.assertEqual(gmail.list_messages("after:100 before:200", label_ids=("INBOX",), page_token="old"),
                         {"messages": [{"id": "abc"}], "nextPageToken": "next"})
        metadata = gmail.get_message("abc", format="metadata", metadata_headers=("From", "Reply-To", "Subject"))
        self.assertNotIn("private body", str(metadata))
        self.assertEqual(metadata["payload"]["headers"], [{"name": "From", "value": "someone@example.com"}])
        self.assertEqual(api.calls[0], ("profile", {"userId": "me", "fields": "emailAddress"}))
        self.assertEqual(api.calls[1], ("list", {"userId": "me", "q": "after:100 before:200",
                                               "labelIds": ["INBOX"], "pageToken": "old", "maxResults": 500,
                                               "fields": "messages/id,nextPageToken"}))
        self.assertEqual(api.calls[2], ("get", {"userId": "me", "id": "abc", "format": "metadata",
                                              "metadataHeaders": ["From", "Reply-To", "Subject"],
                                              "fields": "id,threadId,internalDate,labelIds,snippet,payload/headers"}))

    def test_api_metadata_reaches_luna_without_raw_body(self):
        messages = reader.scan(inbox.GmailReadClient(FakeService()),
                               datetime(2025, 10, 10, 12, tzinfo=timezone.utc),
                               "zhachory1@gmail.com", limit=250)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["headers"], [{"name": "From", "value": "someone@example.com"}])
        with (mock.patch.object(luna.subprocess, "run") as invoke,
              mock.patch.object(luna, "_profile_home", return_value=pathlib.Path("/private/profiles/inbox-luna")),
              mock.patch.object(luna, "_checked_binary", return_value="/fake/hermes")):
            invoke.return_value.returncode = 0
            invoke.return_value.stdout = '{"type":"result","exit_code":0,"text":"{\\"route\\":\\"help\\"}"}\n'
            self.assertEqual(luna.classify("/fake/hermes", pathlib.Path("/private/inbox"),
                                           "2025-10-09", messages[0]), "help")
            self.assertNotIn("private body", invoke.call_args.kwargs["input"])

    def test_exact_thread_projects_plain_text_without_attachment_content(self):
        api = FakeService()
        def thread_get(**kwargs):
            if kwargs["id"] != "thread1":
                raise AssertionError("wrong thread")
            api.calls.append(("thread", kwargs))
            if kwargs["format"] == "metadata":
                return Request({"id": "thread1", "messages": [
                    {"id": "abc", "threadId": "thread1", "sizeEstimate": 300,
                     "internalDate": "1760000000000"}]})
            def encoded(text):
                return base64.urlsafe_b64encode(text.encode()).decode()
            return Request({"id": "thread1", "messages": [{
                "id": "abc", "threadId": "thread1", "internalDate": "1760000000000",
                "payload": {"mimeType": "multipart/mixed", "headers": [
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Subject", "value": "Question"},
                    {"name": "X-Secret", "value": "never surface"}], "parts": [
                        {"mimeType": "text/plain", "body": {"data": encoded("Hello there")}},
                        {"mimeType": "text/plain", "filename": "private.txt",
                         "body": {"data": encoded("private attachment")}},
                        {"mimeType": "text/html", "body": {"data": encoded("hidden html")}}]}}]})
        api.get = thread_get
        projected = inbox.GmailReadClient(api).get_thread("thread1")
        self.assertEqual(projected[0]["text"], "Hello there")
        self.assertNotIn("private attachment", str(projected))
        self.assertNotIn("hidden html", str(projected))
        self.assertNotIn("never surface", str(projected))
        self.assertEqual(api.calls[-1][0], "thread")
        self.assertEqual([call[1]["format"] for call in api.calls if call[0] == "thread"],
                         ["metadata", "full"])

    def test_large_attachment_preflight_never_fetches_full_thread(self):
        api = FakeService()
        def oversized(**kwargs):
            api.calls.append(("thread", kwargs))
            if kwargs["format"] == "full":
                raise AssertionError("full MIME must not be fetched")
            return Request({"id": "thread1", "messages": [
                {"id": "abc", "threadId": "thread1", "sizeEstimate": 1000000,
                 "internalDate": "1760000000000"}]})
        api.get = oversized
        with self.assertRaises(ValueError):
            inbox.GmailReadClient(api).get_thread("thread1")
        self.assertEqual([call[1]["format"] for call in api.calls], ["metadata"])

    def test_oversized_thread_headers_fail_before_model_projection(self):
        api = FakeService()
        raw = base64.urlsafe_b64encode(b"hi").decode()
        api.get = lambda **kwargs: Request({"id": "thread1", "messages": [
            {"id": "abc", "threadId": "thread1", "sizeEstimate": 100,
             "internalDate": "1760000000000", "payload": {"mimeType": "text/plain", "body": {"data": raw},
             "headers": [{"name": "Subject", "value": "x" * 10000}]}}]})
        with self.assertRaises(ValueError):
            inbox.GmailReadClient(api).get_thread("thread1")

    def test_thread_date_changed_between_preflight_and_full_read(self):
        api = FakeService()
        def changed(**kwargs):
            if kwargs["format"] == "metadata":
                return Request({"id": "thread1", "messages": [{"id": "abc", "threadId": "thread1",
                                "sizeEstimate": 100, "internalDate": "1760000000000"}]})
            return Request({"id": "thread1", "messages": [{"id": "abc", "threadId": "thread1",
                            "internalDate": "1760000000001", "payload": {"mimeType": "text/plain",
                            "body": {"data": "SGVsbG8="}}}]})
        api.get = changed
        with self.assertRaises(ValueError):
            inbox.GmailReadClient(api).get_thread("thread1")

    def test_wrong_thread_and_html_only_fail_closed(self):
        api = FakeService()
        api.get = lambda **_: Request({"id": "other", "messages": []})
        with self.assertRaises(ValueError):
            inbox.GmailReadClient(api).get_thread("thread1")
        api.get = lambda **kwargs: Request({"id": "thread1", "messages": [
            {"id": "abc", "threadId": "thread1", "sizeEstimate": 100,
             "internalDate": "1760000000000", "payload": {"mimeType": "text/html", "body": {"data": "PGI+SGk8L2I+"}}}]})
        with self.assertRaises(ValueError):
            inbox.GmailReadClient(api).get_thread("thread1")

    def test_sent_receipt_requests_only_required_headers(self):
        api = FakeService()
        original = api.get
        def sent(**kwargs):
            response = original(**kwargs).response
            response['labelIds'] = ['SENT']
            response['payload']['headers'] = [{"name":"To","value":"them@example.com"},
                                               {"name":"Message-ID","value":"<stable@example.com>"}]
            return Request(response)
        api.get = sent
        result = inbox.GmailReadClient(api).get_message("abc", format="metadata",
            metadata_headers=("From", "To", "Subject", "In-Reply-To", "References", "Message-ID"))
        self.assertEqual([h['name'] for h in result['payload']['headers']], ['To', 'Message-ID'])
        self.assertEqual(api.calls[-1][1]['metadataHeaders'],
                         ['From','To','Subject','In-Reply-To','References','Message-ID'])

    def test_message_with_no_labels_is_skipped_by_reader(self):
        api = FakeService()
        original = api.get
        def unlabeled(**kwargs):
            response = original(**kwargs).response
            response.pop("labelIds")
            return Request(response)
        api.get = unlabeled
        self.assertEqual(inbox.GmailReadClient(api).get_message("abc", format="metadata",
                         metadata_headers=("From", "Reply-To", "Subject"))["labelIds"], [])

    def test_google_transport_sets_bounded_timeout(self):
        with (mock.patch('google.oauth2.credentials.Credentials.from_authorized_user_info',return_value=object()),
              mock.patch('httplib2.Http') as http,
              mock.patch('google_auth_httplib2.AuthorizedHttp') as authorized,
              mock.patch('googleapiclient.discovery.build',return_value=object())):
            inbox._build({'scopes':[inbox.READ_SCOPE]})
            http.assert_called_once_with(timeout=30)
            authorized.assert_called_once()

    def test_only_owner_readonly_token_connects(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = pathlib.Path(tmp) / "gmail-token.json"
            token.write_text('{"scopes":["https://www.googleapis.com/auth/gmail.readonly"],"refresh_token":"fixture"}')
            token.chmod(0o600)
            api = FakeService()
            with mock.patch.object(inbox, "_build", return_value=api) as build:
                with mock.patch.object(pathlib.Path, "read_text", side_effect=AssertionError("path reopened")):
                    self.assertIsInstance(inbox.connect(token), inbox.GmailReadClient)
                build.assert_called_once()
            token.write_text('{"scopes":["https://www.googleapis.com/auth/calendar"],"refresh_token":"fixture"}')
            with self.assertRaises(ValueError):
                inbox.connect(token)
            token.chmod(0o644)
            with self.assertRaises(ValueError):
                inbox.connect(token)


if __name__ == "__main__":
    unittest.main()
