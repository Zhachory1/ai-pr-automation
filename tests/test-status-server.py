#!/usr/bin/env python3
import http.client
import importlib.util
import json
import pathlib
from importlib.machinery import SourceFileLoader
import threading
import unittest
from types import SimpleNamespace
import urllib.error
import urllib.request
from urllib.parse import urlencode
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
loader = SourceFileLoader("status_server", str(ROOT / "bin/status-server"))
spec = importlib.util.spec_from_loader(loader.name, loader)
status_server = importlib.util.module_from_spec(spec)
loader.exec_module(status_server)


class StatusServerTest(unittest.TestCase):
    def test_render_shows_escaped_maintenance_human_queue_item(self):
        rows = [
            [], [], [],
            [["17", "42", "https://github.com/ROKT/repo/pull/7", "Needs <review>",
              '[{"file":"x.py","line":12,"severity":"major","text":"Fix <this>"}]', "09-03 20:43", "maintenance"]],
            [], [], [],
        ]
        with patch.object(status_server, "query", side_effect=rows):
            page = status_server.render()

        self.assertIn("Human review queue (1)", page)
        self.assertIn('href="https://github.com/ROKT/repo/pull/7"', page)
        self.assertIn("Needs &lt;review&gt;", page)
        self.assertIn("Fix &lt;this&gt;", page)
        self.assertIn('/human-reviews/17/reviewed', page)
        self.assertIn('/human-reviews/17/dismissed', page)

    def test_render_shows_escaped_pending_decision_with_actions(self):
        proposal = json.dumps({"summary": "Use <cache>", "decisions": ["keep <value>"],
                               "findings": [{"text": "check <input>"}]})
        provenance = json.dumps({"repo": "ROKT/repo", "written_by": "agent <server>"})
        rows = [
            [], [], [], [],
            [["19", "42", "pr-review", "https://github.com/ROKT/repo/pull/7", proposal, provenance,
              "pending", "09-04 12:00", "", ""]],
            [], [],
        ]
        with patch.object(status_server, "query", side_effect=rows) as query:
            page = status_server.render()

        pending_query = query.call_args_list[4].args[0]
        for alias in ("AS url", "AS proposal", "AS provenance", "AS state", "AS created", "AS started", "AS publish_error"):
            self.assertIn(alias, pending_query)
        self.assertIn("Use &lt;cache&gt;", page)
        self.assertIn("keep &lt;value&gt;", page)
        self.assertIn("check &lt;input&gt;", page)
        self.assertIn("agent &lt;server&gt;", page)
        self.assertIn('/pending-decisions/19/approve', page)
        self.assertIn('/pending-decisions/19/reject', page)

    def test_render_shows_publishing_decision_with_retry_and_error(self):
        rows = [
            [], [], [], [],
            [["19", "42", "pr-review", "https://github.com/ROKT/repo/pull/7", "{}", "{}",
              "publishing", "09-04 12:00", "09-04 12:01", "Hindsight unavailable"]],
            [], [],
        ]
        with patch.object(status_server, "query", side_effect=rows):
            page = status_server.render()

        self.assertIn("Hindsight unavailable", page)
        self.assertIn('/pending-decisions/19/retry', page)
        self.assertNotIn('/pending-decisions/19/approve', page)
        self.assertNotIn('/pending-decisions/19/reject', page)

    def test_malformed_findings_remain_safe_display_data(self):
        self.assertEqual(status_server.findings_html('{"severity": 1}'), '{&quot;severity&quot;: 1}')
        self.assertIn('1 — x.py:12', status_server.findings_html('[{"file":"x.py","line":12,"severity":1}]'))

    def test_human_review_write_uses_separate_local_table(self):
        with patch.object(status_server, "_psql", return_value=SimpleNamespace(returncode=0, stdout="17\n")) as psql:
            self.assertEqual(status_server.update_human_review_state(17, "reviewed"), "reviewed human-review #17")
        self.assertIn("UPDATE pending_maintenance_reviews", psql.call_args.args[1])
        self.assertIn("reviewed_at = now()", psql.call_args.args[1])
        with self.assertRaises(ValueError):
            status_server.update_human_review_state(17, "approved")

    def test_approve_claims_then_retains_stored_decision_before_approving(self):
        decision = json.dumps({"id": 19, "request_id": 42, "kind": "pr-review",
                               "proposal": {"summary": "keep", "decisions": ["decision"]},
                               "provenance": {"run_id": "run-1", "written_by": "agent-server"}})
        responses = [SimpleNamespace(returncode=0, stdout=decision, stderr=""),
                     SimpleNamespace(returncode=0, stdout="19\n", stderr="")]
        with patch.object(status_server, "_psql", side_effect=responses) as psql, \
                patch.object(status_server, "urlopen", return_value=SimpleNamespace(
                    read=lambda: b'{"success":true,"async":false,"bank_id":"fleet-shared","items_count":1}',
                    close=lambda: None)) as retain:
            self.assertEqual(status_server.approve_pending_decision(19), "approved pending-decision #19")

        self.assertIn("state = 'publishing'", psql.call_args_list[0].args[1])
        self.assertIn("state = 'pending'", psql.call_args_list[0].args[1])
        self.assertIn("state = 'approved'", psql.call_args_list[1].args[1])
        request = retain.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "http://hindsight:8888/v1/default/banks/fleet-shared/memories")
        self.assertFalse(payload["async"])
        self.assertNotIn("update_mode", payload)
        self.assertEqual(payload["items"][0]["update_mode"], "replace")
        self.assertEqual(payload["items"][0]["document_id"], "pending-decision-19")
        self.assertEqual(retain.call_args.kwargs["timeout"], 60)
        self.assertEqual(payload["items"][0]["metadata"],
                         {"pending_decision_id": "19", "request_id": "42", "kind": "pr-review"})
        self.assertEqual(json.loads(payload["items"][0]["content"]),
                         {"pending_decision_id": 19, "proposal": {"summary": "keep", "decisions": ["decision"]},
                          "provenance": {"run_id": "run-1", "written_by": "agent-server"}})

    def test_stale_initial_claim_does_not_call_hindsight(self):
        with patch.object(status_server, "_psql", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
                patch.object(status_server, "urlopen") as retain:
            self.assertEqual(status_server.approve_pending_decision(19), "#19 not pending")

        retain.assert_not_called()

    def test_retain_failure_stays_publishing_with_retry(self):
        decision = json.dumps({"id": 19, "request_id": 42, "kind": "pr-review",
                               "proposal": {}, "provenance": {}})
        responses = [SimpleNamespace(returncode=0, stdout=decision, stderr=""),
                     SimpleNamespace(returncode=0, stdout="", stderr="")]
        with patch.object(status_server, "_psql", side_effect=responses) as psql, \
                patch.object(status_server, "urlopen", side_effect=urllib.error.URLError("down")):
            self.assertEqual(status_server.approve_pending_decision(19),
                             "publish failed; retry pending-decision #19")

        self.assertIn("state = 'publishing'", psql.call_args_list[1].args[1])
        self.assertIn("publish_error", psql.call_args_list[1].args[1])

    def test_unconfirmed_hindsight_response_stays_publishing(self):
        decision = json.dumps({"id": 19, "request_id": 42, "kind": "pr-review",
                               "proposal": {}, "provenance": {}})
        for response_body in (b'{"success":false,"async":false,"bank_id":"fleet-shared","items_count":1}',
                              b"not json"):
            with self.subTest(response_body=response_body), \
                    patch.object(status_server, "_psql", side_effect=[
                        SimpleNamespace(returncode=0, stdout=decision, stderr=""),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                    ]) as psql, \
                    patch.object(status_server, "urlopen", return_value=SimpleNamespace(
                        read=lambda body=response_body: body, close=lambda: None)):
                self.assertEqual(status_server.approve_pending_decision(19),
                                 "publish failed; retry pending-decision #19")

            self.assertIn("state = 'publishing'", psql.call_args_list[1].args[1])
            self.assertIn("publish_error", psql.call_args_list[1].args[1])

    def test_retry_reuses_publishing_row_and_document_id(self):
        decision = json.dumps({"id": 19, "request_id": 42, "kind": "pr-review",
                               "proposal": {}, "provenance": {}})
        responses = [SimpleNamespace(returncode=0, stdout=decision, stderr=""),
                     SimpleNamespace(returncode=0, stdout="19\n", stderr="")]
        with patch.object(status_server, "_psql", side_effect=responses) as psql, \
                patch.object(status_server, "urlopen", return_value=SimpleNamespace(
                    read=lambda: b'{"success":true,"async":false,"bank_id":"fleet-shared","items_count":1}',
                    close=lambda: None)) as retain:
            self.assertEqual(status_server.retry_pending_decision(19), "approved pending-decision #19")

        self.assertIn("state = 'publishing'", psql.call_args_list[0].args[1])
        self.assertEqual(json.loads(retain.call_args.args[0].data)["items"][0]["document_id"], "pending-decision-19")

    def test_reject_pending_decision_does_not_call_hindsight(self):
        with patch.object(status_server, "_psql", return_value=SimpleNamespace(returncode=0, stdout="19\n", stderr="")) as psql, \
                patch.object(status_server, "urlopen") as retain:
            self.assertEqual(status_server.reject_pending_decision(19), "rejected pending-decision #19")

        self.assertIn("state = 'rejected'", psql.call_args.args[1])
        self.assertIn("state = 'pending'", psql.call_args.args[1])
        retain.assert_not_called()

    def test_post_changes_only_local_queue_state_from_local_origin(self):
        server = status_server.ThreadingHTTPServer(("127.0.0.1", 0), status_server.Handler)
        old_hosts, old_origins = status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS
        origin = f"http://127.0.0.1:{server.server_port}"
        status_server.ALLOWED_HOSTS = {f"127.0.0.1:{server.server_port}"}
        status_server.ALLOWED_ORIGINS = {origin}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"{origin}/human-reviews/17/reviewed"
            body = urlencode({"token": status_server.CSRF_TOKEN}).encode()
            request = urllib.request.Request(url, data=body, method="POST", headers={"Origin": origin})
            with patch.object(status_server, "update_human_review_state", return_value="reviewed human-review #17") as update, \
                    patch.object(status_server, "render", return_value="ok"):
                response = urllib.request.urlopen(request)
                self.assertEqual(response.status, 200)
                update.assert_called_once_with(17, "reviewed")

            bad_origin = urllib.request.Request(url, data=body, method="POST", headers={"Origin": "https://example.com"})
            with patch.object(status_server, "update_human_review_state") as update:
                with self.assertRaises(urllib.error.HTTPError) as response:
                    urllib.request.urlopen(bad_origin)
                self.assertEqual(response.exception.code, 403)
                response.exception.close()
                update.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS = old_hosts, old_origins

    def test_pending_decision_post_is_csrf_guarded(self):
        server = status_server.ThreadingHTTPServer(("127.0.0.1", 0), status_server.Handler)
        old_hosts, old_origins = status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS
        origin = f"http://127.0.0.1:{server.server_port}"
        status_server.ALLOWED_HOSTS = {f"127.0.0.1:{server.server_port}"}
        status_server.ALLOWED_ORIGINS = {origin}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"{origin}/pending-decisions/19/approve"
            body = urlencode({"token": status_server.CSRF_TOKEN}).encode()
            request = urllib.request.Request(url, data=body, method="POST", headers={"Origin": origin})
            with patch.object(status_server, "approve_pending_decision", return_value="approved pending-decision #19") as approve, \
                    patch.object(status_server, "render", return_value="ok"):
                response = urllib.request.urlopen(request)
                self.assertEqual(response.status, 200)
                approve.assert_called_once_with(19)

            bad_token = urllib.request.Request(
                url, data=urlencode({"token": "bad"}).encode(), method="POST", headers={"Origin": origin})
            with patch.object(status_server, "approve_pending_decision") as approve:
                with self.assertRaises(urllib.error.HTTPError) as response:
                    urllib.request.urlopen(bad_token)
                self.assertEqual(response.exception.code, 403)
                response.exception.close()
                approve.assert_not_called()

            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            with patch.object(status_server, "approve_pending_decision") as approve:
                connection.request("POST", "/pending-decisions/19/approve", body,
                                   {"Host": "example.com", "Origin": origin,
                                    "Content-Type": "application/x-www-form-urlencoded"})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                approve.assert_not_called()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS = old_hosts, old_origins


if __name__ == "__main__":
    unittest.main()
