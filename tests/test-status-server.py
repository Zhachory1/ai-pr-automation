#!/usr/bin/env python3
import hashlib
import http.client
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
from importlib.machinery import SourceFileLoader
import threading
import unittest
from types import SimpleNamespace
import urllib.error
import urllib.request
from urllib.parse import urlencode
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTH_TMP = tempfile.TemporaryDirectory()
PASSWORD_FILE = pathlib.Path(AUTH_TMP.name) / "password"
SESSION_FILE = pathlib.Path(AUTH_TMP.name) / "session"
PASSWORD_FILE.write_text("test-controller-password\n")
SESSION_FILE.write_text("0123456789abcdef0123456789abcdef\n")
PASSWORD_FILE.chmod(0o600)
SESSION_FILE.chmod(0o600)
os.environ["FLEET_CONTROLLER_USERNAME"] = "fleet"
os.environ["FLEET_CONTROLLER_PASSWORD_FILE"] = str(PASSWORD_FILE)
os.environ["FLEET_CONTROLLER_SESSION_SECRET_FILE"] = str(SESSION_FILE)
loader = SourceFileLoader("status_server", str(ROOT / "bin/status-server"))
spec = importlib.util.spec_from_loader(loader.name, loader)
status_server = importlib.util.module_from_spec(spec)
loader.exec_module(status_server)


class StatusServerTest(unittest.TestCase):
    def test_render_shows_generic_run_ids(self):
        rows = [
            [["7", "pr-review", "run-<active>", "repo#7@head", "00:01:02"]], [],
            [["6", "doc-write", "done", "run-finished", "doc:6", "", "09-18 10:00"]],
            [], [], [], [], [], [],
        ]
        with patch.object(status_server, "query", side_effect=rows):
            page = status_server.render()
        self.assertIn("Runs &amp; Queue", page)
        self.assertIn("run-&lt;active&gt;", page)
        self.assertIn("run-finished", page)
        self.assertIn("run_id", page)

    def test_fleet_proxy_host_and_origin_are_allowed(self):
        self.assertIn(f"fleet.localhost:{status_server.PUBLIC_PORT}", status_server.ALLOWED_HOSTS)
        self.assertIn(f"https://fleet.localhost:{status_server.PUBLIC_PORT}", status_server.ALLOWED_ORIGINS)

    def test_trusted_local_proxy_needs_marker_and_exact_host(self):
        class Request:
            headers = {"X-Fleet-Local-Proxy": "1",
                       "Host": f"fleet.localhost:{status_server.PUBLIC_PORT}"}
        with patch.object(status_server, "TRUST_LOCAL_PROXY", True):
            self.assertEqual(status_server.Handler._actor(Request()), status_server.AUTH_USERNAME)
            Request.headers["Host"] = "example.com"
            self.assertIsNone(status_server.Handler._actor(Request()))
            Request.headers = {"Host": f"fleet.localhost:{status_server.PUBLIC_PORT}"}
            self.assertIsNone(status_server.Handler._actor(Request()))

    def test_render_shows_escaped_pr_safety_human_queue_item(self):
        # query order: running, queued, recent, human_review, pending, today, capped, swe, docs
        rows = [
            [], [], [],
            [["17", "42", "https://github.com/ROKT/repo/pull/7", "Needs <review>",
              '[{"file":"x.py","line":12,"severity":"major","claim":"Fix <this>","risk":"break","recommended_remediation":"test"}]',
              "/private/handoff.md", "09-03 20:43", "pr-safety-review", "[]", ""]],
            [], [], [], [], [],
        ]
        with patch.object(status_server, "query", side_effect=rows):
            page = status_server.render()

        self.assertIn("Human review queue (1)", page)
        self.assertIn('href="https://github.com/ROKT/repo/pull/7"', page)
        self.assertIn("Needs &lt;review&gt;", page)
        self.assertIn("Fix &lt;this&gt;", page)
        self.assertIn("/private/handoff.md", page)
        self.assertIn("pr-safety", page)
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
            [], [], [], [],
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
            [], [], [], [],
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

    def test_swe_handoff_binds_repository_from_controller_identity(self):
        with tempfile.TemporaryDirectory() as td:
            handoff = pathlib.Path(td) / "handoff.md"
            handoff.write_text("<!-- pr-safety identity\noperation_id: op\nrepo: ROKT/ml\npr: 7\n-->\n")
            with patch.object(status_server, "HANDOFF_ROOT", td), \
                    patch.object(status_server, "_swe_enqueue", return_value="queued") as enqueue:
                self.assertEqual(status_server.swe_implement_handoff(str(handoff)), "queued")
            self.assertEqual(enqueue.call_args.args[0], {
                "source":"handoff", "handoff_path":os.path.realpath(handoff), "repo":"ROKT/ml", "no_pr":False})
            handoff.write_text("missing identity\n")
            with patch.object(status_server, "HANDOFF_ROOT", td), self.assertRaises(ValueError):
                status_server.swe_implement_handoff(str(handoff))

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

    def test_startup_rejects_missing_auth_secrets(self):
        env = os.environ.copy()
        env.pop("FLEET_CONTROLLER_PASSWORD_FILE", None)
        env.pop("FLEET_CONTROLLER_SESSION_SECRET_FILE", None)
        result = subprocess.run([
            "python3", "-c", "import runpy; runpy.run_path('bin/status-server', run_name='status_config_test')"
        ], cwd=ROOT, env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FLEET_CONTROLLER_PASSWORD_FILE", result.stderr)

    def test_http_server_worker_count_is_bounded(self):
        server = status_server.BoundedHTTPServer(("127.0.0.1", 0), status_server.Handler)
        try:
            for _ in range(status_server.MAX_HTTP_WORKERS):
                self.assertTrue(server._workers.acquire(blocking=False))
            self.assertFalse(server._workers.acquire(blocking=False))
        finally:
            for _ in range(status_server.MAX_HTTP_WORKERS):
                server._workers.release()
            server.server_close()

    def test_secret_reader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "target"
            link = pathlib.Path(directory) / "link"
            target.write_text("x" * 32)
            link.symlink_to(target)
            with patch.dict(os.environ, {"TEST_SECRET_FILE": str(link)}), \
                    self.assertRaisesRegex(RuntimeError, "unreadable"):
                status_server.read_secret("TEST_SECRET_FILE", 16)

    def test_sessions_are_signed_bounded_and_reject_tampering(self):
        token = status_server.issue_session(now=1000)
        cookie = f"{status_server.SESSION_COOKIE}={token}"
        self.assertEqual(status_server.session_actor(cookie, now=1001), "fleet")
        self.assertIsNone(status_server.session_actor(cookie, now=1000 + status_server.SESSION_SECONDS))
        self.assertIsNone(status_server.session_actor(cookie + "x", now=1001))
        future = status_server.issue_session(now=2000)
        self.assertIsNone(status_server.session_actor(
            f"{status_server.SESSION_COOKIE}={future}", now=1000))

    def test_login_is_only_anonymous_page_and_sets_secure_cookie(self):
        server = status_server.BoundedHTTPServer(("127.0.0.1", 0), status_server.Handler)
        old_hosts, old_origins = status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS
        host = f"127.0.0.1:{server.server_port}"
        origin = f"https://{host}"
        status_server.ALLOWED_HOSTS = {host}
        status_server.ALLOWED_ORIGINS = {origin}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request("GET", "/login")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertIn(b"Fleet Controller", response.read())

            with patch.object(status_server.Handler, "cached_page") as page:
                connection.request("GET", "/")
                response = connection.getresponse()
                self.assertEqual(response.status, 303)
                self.assertEqual(response.getheader("Location"), "/login")
                response.read()
                page.assert_not_called()

            wrong = urlencode({"username": "fleet", "password": "wrong-password-value"})
            connection.request("POST", "/login", wrong, {
                "Host": host, "Origin": origin, "Content-Type": "application/x-www-form-urlencoded"})
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            self.assertIsNone(response.getheader("Set-Cookie"))
            response.read()

            body = urlencode({"username": "fleet", "password": "test-controller-password"})
            connection.request("POST", "/login", body, {
                "Host": host, "Origin": origin, "Content-Type": "application/x-www-form-urlencoded"})
            response = connection.getresponse()
            self.assertEqual(response.status, 303)
            self.assertEqual(response.getheader("Location"), "/")
            cookie = response.getheader("Set-Cookie")
            self.assertTrue(cookie.startswith("__Host-fleet_controller_session="))
            self.assertIn("Secure", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            self.assertIn(f"Max-Age={status_server.SESSION_SECONDS}", cookie)
            response.read()

            session = cookie.split(";", 1)[0]
            with patch.object(status_server.Handler, "cached_page", return_value=b"ok"):
                connection.request("GET", "/", headers={"Cookie": session})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                self.assertEqual(response.read(), b"ok")
            connection.request("POST", "/logout", urlencode({"token": status_server.CSRF_TOKEN}), {
                "Host": host, "Origin": origin, "Cookie": session,
                "Content-Type": "application/x-www-form-urlencoded"})
            response = connection.getresponse()
            self.assertEqual(response.status, 303)
            self.assertIn("Max-Age=0", response.getheader("Set-Cookie"))
            response.read()
            self.assertIsNone(status_server.session_actor(session))
            connection.close()
            self.assertIn("r.status===401", status_server.LIVE_JS)
            self.assertIn("pathname==='/logout'", status_server.LIVE_JS)
        finally:
            server.shutdown()
            server.server_close()
            status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS = old_hosts, old_origins

    def test_anonymous_mutation_and_tampered_cookie_are_rejected(self):
        server = status_server.BoundedHTTPServer(("127.0.0.1", 0), status_server.Handler)
        old_hosts, old_origins = status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS
        origin = f"https://127.0.0.1:{server.server_port}"
        status_server.ALLOWED_HOSTS = {f"127.0.0.1:{server.server_port}"}
        status_server.ALLOWED_ORIGINS = {origin}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/cancel"
            body = urlencode({"token": status_server.CSRF_TOKEN, "id": "7"}).encode()
            for cookie in (None, f"{status_server.SESSION_COOKIE}=tampered"):
                headers = {"Origin": origin}
                if cookie:
                    headers["Cookie"] = cookie
                request = urllib.request.Request(url, data=body, method="POST", headers=headers)
                with patch.object(status_server, "cancel_queued") as cancel, \
                        self.assertRaises(urllib.error.HTTPError) as response:
                    urllib.request.urlopen(request)
                self.assertEqual(response.exception.code, 401)
                response.exception.close()
                cancel.assert_not_called()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.putrequest("POST", "/cancel", skip_host=True)
            connection.putheader("Host", f"127.0.0.1:{server.server_port}")
            connection.putheader("Content-Length", "-1")
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS = old_hosts, old_origins

    def test_audit_contains_actor_action_and_outcome_only(self):
        with patch("builtins.print") as output:
            status_server.audit("fleet", "/cancel?token=must-not-log", "ok")
        record = json.loads(output.call_args.args[0])
        self.assertEqual(record["actor"], "fleet")
        self.assertEqual(record["action"], "/cancel")
        self.assertEqual(record["outcome"], "ok")
        self.assertEqual(set(record), {"event", "timestamp", "actor", "action", "outcome"})
        self.assertNotIn("must-not-log", output.call_args.args[0])

    def test_mutation_result_classification_is_not_false_success(self):
        self.assertTrue(status_server.result_succeeded("injected pr-review owner/repo#7"))
        self.assertTrue(status_server.result_succeeded("already queued/running (deduped)"))
        for result in ("rejected: bad input", "publish failed; retry pending-decision #19",
                       "#19 not pending", "#7 not cancelled", "NOT queued: daily cap"):
            with self.subTest(result=result):
                self.assertFalse(status_server.result_succeeded(result))

    def test_post_changes_only_local_queue_state_from_local_origin(self):
        server = status_server.BoundedHTTPServer(("127.0.0.1", 0), status_server.Handler)
        old_hosts, old_origins = status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS
        origin = f"https://127.0.0.1:{server.server_port}"
        status_server.ALLOWED_HOSTS = {f"127.0.0.1:{server.server_port}"}
        status_server.ALLOWED_ORIGINS = {origin}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/human-reviews/17/reviewed"
            body = urlencode({"token": status_server.CSRF_TOKEN}).encode()
            cookie = f"{status_server.SESSION_COOKIE}={status_server.issue_session()}"
            request = urllib.request.Request(url, data=body, method="POST", headers={
                "Origin": origin, "Cookie": cookie})
            with patch.object(status_server, "update_human_review_state", return_value="reviewed human-review #17") as update, \
                    patch.object(status_server, "render", return_value="ok"):
                response = urllib.request.urlopen(request)
                self.assertEqual(response.status, 200)
                update.assert_called_once_with(17, "reviewed")

            bad_origin = urllib.request.Request(url, data=body, method="POST", headers={
                "Origin": "https://example.com", "Cookie": cookie})
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
        server = status_server.BoundedHTTPServer(("127.0.0.1", 0), status_server.Handler)
        old_hosts, old_origins = status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS
        origin = f"https://127.0.0.1:{server.server_port}"
        status_server.ALLOWED_HOSTS = {f"127.0.0.1:{server.server_port}"}
        status_server.ALLOWED_ORIGINS = {origin}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/pending-decisions/19/approve"
            body = urlencode({"token": status_server.CSRF_TOKEN}).encode()
            cookie = f"{status_server.SESSION_COOKIE}={status_server.issue_session()}"
            request = urllib.request.Request(url, data=body, method="POST", headers={
                "Origin": origin, "Cookie": cookie})
            with patch.object(status_server, "approve_pending_decision", return_value="approved pending-decision #19") as approve, \
                    patch.object(status_server, "render", return_value="ok"):
                response = urllib.request.urlopen(request)
                self.assertEqual(response.status, 200)
                approve.assert_called_once_with(19)

            bad_token = urllib.request.Request(
                url, data=urlencode({"token": "bad"}).encode(), method="POST", headers={
                    "Origin": origin, "Cookie": cookie})
            with patch.object(status_server, "approve_pending_decision") as approve:
                with self.assertRaises(urllib.error.HTTPError) as response:
                    urllib.request.urlopen(bad_token)
                self.assertEqual(response.exception.code, 403)
                response.exception.close()
                approve.assert_not_called()

            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            with patch.object(status_server, "approve_pending_decision") as approve:
                connection.request("POST", "/pending-decisions/19/approve", body,
                                   {"Host": "example.com", "Origin": origin, "Cookie": cookie,
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

    # --- doc-writer: enqueue cap + refine close-on-cap (MAJOR-1 / M1) ---

    def _cp(self, rc=0, out=""):
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    def test_doc_write_rejects_bad_type_and_empty(self):
        with self.assertRaises(ValueError):
            status_server.doc_write("bogus", "t", "r")
        with self.assertRaises(ValueError):
            status_server.doc_write("seprd", "", "r")
        with self.assertRaises(ValueError):
            status_server.doc_write("seprd", "t", "")

    def test_doc_write_cap_reached_reports_not_enqueued(self):
        # _doc_enqueue: INSERT ... RETURNING returns empty when the daily cap predicate fails.
        with patch.object(status_server, "_psql", return_value=self._cp(0, "")):
            msg = status_server.doc_write("seprd", "My Doc", "reqs")
        self.assertIn("not enqueued", msg)
        self.assertIn("cap", msg)

    def test_doc_refine_closes_row_only_once(self):
        # A pending doc item; the close UPDATE ... RETURNING id succeeds once, then the enqueue works.
        proposal = json.dumps({"kind": "doc-open-questions", "doc_type": "seprd",
                               "title": "My Doc", "draft_path": "/stage/x.md", "round": 1})
        calls = [self._cp(0, proposal),      # SELECT proposal
                 self._cp(0, "7"),           # UPDATE ... RETURNING id -> owns the close
                 self._cp(0, "1")]           # _doc_enqueue INSERT ... RETURNING 1
        with patch.object(status_server, "_psql", side_effect=calls):
            msg = status_server.doc_refine(7, "here are answers", finalize=False)
        self.assertIn("refining (round 2)", msg)

    def test_doc_refine_double_click_does_not_double_enqueue(self):
        # Second refine on an already-closed row: the close UPDATE returns nothing -> raise, no enqueue.
        proposal = json.dumps({"kind": "doc-open-questions", "doc_type": "seprd",
                               "title": "My Doc", "draft_path": "/stage/x.md", "round": 1})
        calls = [self._cp(0, proposal),      # SELECT proposal (still readable)
                 self._cp(0, "")]            # UPDATE ... RETURNING id -> empty (already handled)
        with patch.object(status_server, "_psql", side_effect=calls) as m:
            with self.assertRaises(RuntimeError):
                status_server.doc_refine(7, "answers", finalize=False)
        # only SELECT + the losing UPDATE ran; no enqueue INSERT.
        self.assertEqual(m.call_count, 2)

    def test_doc_refine_closed_but_capped_reports_not_queued(self):
        # Row closes (we own it) but the next-round enqueue is capped: must NOT silently drop.
        proposal = json.dumps({"kind": "doc-open-questions", "doc_type": "seprd",
                               "title": "My Doc", "draft_path": "/stage/x.md", "round": 1})
        calls = [self._cp(0, proposal),      # SELECT
                 self._cp(0, "7"),           # UPDATE close -> owned
                 self._cp(0, "")]            # enqueue INSERT -> empty (cap reached)
        with patch.object(status_server, "_psql", side_effect=calls):
            msg = status_server.doc_refine(7, "answers", finalize=False)
        self.assertIn("NOT queued", msg)
        self.assertIn("Resubmit", msg)

    def test_publication_preview_is_exact_escaped_and_server_bound(self):
        content = b"# Exact <script>alert(1)</script>\n"
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "requests" / "42"
            path.mkdir(parents=True)
            (path / "publish.md").write_bytes(content)
            with patch.object(status_server, "DOC_WRITER_STAGE_DIR", directory):
                page = status_server.human_review_table([[
                    "17", "42", "", "Ready <now>", "[]", "", "09-15 10:00",
                    "doc-publication-approval", "[]", "", "requests/42/publish.md",
                    "dd-2026-09-15-exact.md", digest, "legacy:" + "a" * 64,
                ]])

        self.assertIn("Ready &lt;now&gt;", page)
        self.assertIn("# Exact &lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert", page)
        self.assertIn('/doc-publications/17/publish', page)
        self.assertIn('/doc-publications/17/dismiss', page)
        self.assertNotIn(f'name=digest value="{digest}"', page)

    def test_publication_preview_rejects_symlink_or_bad_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            path = root / "requests" / "42"
            path.mkdir(parents=True)
            (path / "publish.md").symlink_to("/etc/passwd")
            with patch.object(status_server, "DOC_WRITER_STAGE_DIR", directory):
                with self.assertRaises(ValueError):
                    status_server._publication_bytes("requests/42/publish.md", "a" * 64)
            (path / "publish.md").unlink()
            (path / "publish.md").write_text("different")
            with patch.object(status_server, "DOC_WRITER_STAGE_DIR", directory):
                with self.assertRaises(ValueError):
                    status_server._publication_bytes("requests/42/publish.md", "a" * 64)
            invalid = b"\xff"
            (path / "publish.md").write_bytes(invalid)
            with patch.object(status_server, "DOC_WRITER_STAGE_DIR", directory):
                with self.assertRaisesRegex(ValueError, "UTF-8"):
                    status_server._publication_bytes(
                        "requests/42/publish.md", hashlib.sha256(invalid).hexdigest())

    def test_publication_decision_uses_only_review_id(self):
        lookup = json.dumps({"staged_path": "requests/42/publish.md", "content_digest": "a" * 64})
        with patch.object(status_server, "_psql", side_effect=[self._cp(0, lookup), self._cp(0, "42\n")]) as psql, \
                patch.object(status_server, "_publication_bytes", return_value=b"exact") as validate:
            self.assertEqual(status_server.doc_publication_decide(17, "publish"),
                             "queued approved publication request #42")
        validate.assert_called_once_with("requests/42/publish.md", "a" * 64)
        self.assertEqual(psql.call_args_list[1].args[0], ["-v", "rid=17"])
        sql = psql.call_args_list[1].args[1]
        self.assertIn("p.state='awaiting_approval'", sql)
        self.assertIn("payload=jsonb_set", sql)
        self.assertIn("dedupe_key='doc-publish:'", sql)
        self.assertNotIn("target_path=:", sql)
        with patch.object(status_server, "_psql", return_value=self._cp(0, "42\n")) as psql:
            self.assertEqual(status_server.doc_publication_decide(17, "dismiss"),
                             "dismissed publication request #42")
        self.assertIn("state='dismissed'", psql.call_args.args[1])

    def test_publication_approval_rejects_changed_stage_before_update(self):
        lookup = json.dumps({"staged_path": "requests/42/publish.md", "content_digest": "a" * 64})
        with patch.object(status_server, "_psql", side_effect=[self._cp(0, lookup), self._cp(0, "17\n")]) as psql, \
                patch.object(status_server, "_publication_bytes", side_effect=ValueError("digest mismatch")):
            with self.assertRaises(ValueError):
                status_server.doc_publication_decide(17, "publish")
        self.assertEqual(psql.call_count, 2)
        invalidation = psql.call_args_list[1].args[1]
        self.assertIn("state='invalid'", invalidation)
        self.assertIn("state='dismissed'", invalidation)

    def test_publication_post_is_csrf_guarded(self):
        server = status_server.BoundedHTTPServer(("127.0.0.1", 0), status_server.Handler)
        old_hosts, old_origins = status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS
        origin = f"https://127.0.0.1:{server.server_port}"
        status_server.ALLOWED_HOSTS = {f"127.0.0.1:{server.server_port}"}
        status_server.ALLOWED_ORIGINS = {origin}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/doc-publications/17/publish"
            body = urlencode({"token": status_server.CSRF_TOKEN}).encode()
            cookie = f"{status_server.SESSION_COOKIE}={status_server.issue_session()}"
            request = urllib.request.Request(url, data=body, method="POST", headers={
                "Origin": origin, "Cookie": cookie})
            with patch.object(status_server, "doc_publication_decide", return_value="queued") as decide, \
                    patch.object(status_server, "render", return_value="ok"):
                self.assertEqual(urllib.request.urlopen(request).status, 200)
                decide.assert_called_once_with(17, "publish")
            bad = urllib.request.Request(url, data=urlencode({"token": "bad"}).encode(),
                                         method="POST", headers={"Origin": origin, "Cookie": cookie})
            with patch.object(status_server, "doc_publication_decide") as decide:
                with self.assertRaises(urllib.error.HTTPError) as response:
                    urllib.request.urlopen(bad)
                self.assertEqual(response.exception.code, 403)
                response.exception.close()
                decide.assert_not_called()
            missing_origin = urllib.request.Request(url, data=body, method="POST", headers={"Cookie": cookie})
            with patch.object(status_server, "doc_publication_decide") as decide:
                with self.assertRaises(urllib.error.HTTPError) as response:
                    urllib.request.urlopen(missing_origin)
                self.assertEqual(response.exception.code, 403)
                response.exception.close()
                decide.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            status_server.ALLOWED_HOSTS, status_server.ALLOWED_ORIGINS = old_hosts, old_origins


if __name__ == "__main__":
    unittest.main()
