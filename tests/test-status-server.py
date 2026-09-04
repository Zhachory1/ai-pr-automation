#!/usr/bin/env python3
import importlib.util
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
              '[{"file":"x.py","line":12,"severity":"major","text":"Fix <this>"}]', "09-03 20:43"]],
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


if __name__ == "__main__":
    unittest.main()
