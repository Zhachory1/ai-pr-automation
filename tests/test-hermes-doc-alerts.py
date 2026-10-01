#!/usr/bin/env python3
import importlib.util
import os
import pathlib
import plistlib
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.modules.setdefault("psycopg", types.SimpleNamespace(connect=None))
sys.modules.setdefault("psycopg.rows", types.SimpleNamespace(dict_row=None))
spec = importlib.util.spec_from_file_location("hermes_controller", ROOT / "scripts/hermes-controller.py")
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
notifier = runpy.run_path(str(ROOT / "bin/hermes-doc-alert-notify"))
notify = notifier["notify"]


class AlertsTest(unittest.TestCase):
    def test_controller_spools_only_committed_pending_reviews_and_recovers_on_restart(self):
        rows = [{"id": 13, "kind": "doc-open-questions"},
                {"id": 14, "kind": "doc-publication-approval"}]

        class DB:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def execute(self, query, params=None):
                self.query = query
                if "id=ANY" in query:
                    self.rows = [row for row in rows if row["id"] in params[0]]
                else:
                    self.rows = list(rows)
                return self
            def fetchall(self): return self.rows

        instance = controller.Controller.__new__(controller.Controller)
        db = DB()
        instance.connect = lambda: db
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"DOC_ALERT_SPOOL_DIR": directory}):
            instance.queue_doc_alerts()
            self.assertIn("state='pending'", db.query)
            self.assertEqual(sorted(p.name for p in pathlib.Path(directory).iterdir()),
                             ["doc-open-questions-13.pending", "doc-publication-approval-14.pending"])
            (pathlib.Path(directory) / "doc-open-questions-13.pending").rename(
                pathlib.Path(directory) / "doc-open-questions-13.sent")
            instance.queue_doc_alerts()
            self.assertFalse((pathlib.Path(directory) / "doc-open-questions-13.pending").exists())
            rows.append({"id": 15, "kind": "doc-open-questions"})
            instance.queue_doc_alerts()
            self.assertTrue((pathlib.Path(directory) / "doc-open-questions-15.pending").exists())
            rows.pop()
            instance.queue_doc_alerts()
            self.assertTrue((pathlib.Path(directory) / "doc-open-questions-15.cancelled").exists())
            rows.insert(0, {"id": 12, "kind": "doc-open-questions"})
            instance.queue_doc_alerts()
            self.assertTrue((pathlib.Path(directory) / "doc-open-questions-12.pending").exists())

    def test_known_daemon_failure_retries_but_never_changes_doc_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "doc-open-questions-7.pending").touch()
            sent = []
            def unavailable(): raise ConnectionError("daemon down")
            for suffix in ("retry-1", "retry-2"):
                notify(root, lambda text: sent.append(text), unavailable)
                self.assertTrue((root / f"doc-open-questions-7.{suffix}").exists())
            notify(root, lambda text: sent.append(text) or types.SimpleNamespace(returncode=0), lambda: None)
            self.assertTrue((root / "doc-open-questions-7.sent").exists())
            notify(root, lambda text: sent.append(text), lambda: None)
            self.assertEqual(sent, ["Document review #7 needs answers. Open Documents in the status UI."])

    def test_uncertain_delivery_is_held_for_manual_check_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "doc-publication-approval-9.pending").touch()
            calls = []
            def send(text):
                calls.append(text)
                raise TimeoutError("unknown result")
            notify(root, send, lambda: None)
            notify(root, send, lambda: None)
            self.assertEqual(len(calls), 1)
            self.assertTrue((root / "doc-publication-approval-9.uncertain").exists())
            self.assertNotIn("secret", calls[0])
            self.assertIn("publication review", calls[0])
            (root / "doc-open-questions-11.pending").touch()
            notify(root, lambda text: types.SimpleNamespace(returncode=1), lambda: None)
            self.assertTrue((root / "doc-open-questions-11.uncertain").exists())

    def test_installed_launchd_template_points_to_host_only_spool_and_hermes(self):
        template = (ROOT / "launchd/com.example.ai-pr-automation-doc-alert.plist.template").read_text()
        for key, value in {"__HERMES_USER__": "hermes-agent", "__SERVICE_HOME__": "/Users/hermes-agent",
                           "__HERMES_HOME__": "/Users/hermes-agent/.hermes",
                           "__HERMES_BIN__": "/Users/hermes-agent/.local/bin/hermes",
                           "__SUPPORT_ROOT__": "/usr/local/libexec/ai-pr-automation",
                           "__DOC_ALERT_SPOOL__": "/Users/Shared/doc-writer/alerts",
                           "__LOG_ROOT__": "/var/log/hermes"}.items():
            template = template.replace(key, value)
        config = plistlib.loads(template.encode())
        self.assertEqual(config["UserName"], "hermes-agent")
        self.assertEqual(config["EnvironmentVariables"]["DOC_ALERT_SPOOL_DIR"], "/Users/Shared/doc-writer/alerts")
        self.assertIn("hermes-doc-alert-notify", config["ProgramArguments"][-1])
        self.assertEqual(config["StartInterval"], 60)

    def test_missing_hermes_executable_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "doc-publication-approval-10.pending").touch()
            notify(root, lambda text: (_ for _ in ()).throw(FileNotFoundError()), lambda: None)
            self.assertTrue((root / "doc-publication-approval-10.retry-1").exists())

    def test_signal_destination_rejects_wrong_recipient_and_duplicate_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            values = {"SIGNAL_ACCOUNT": "+15551234567", "SIGNAL_HOME_CHANNEL": "+15557654321",
                      "SIGNAL_HTTP_URL": "http://127.0.0.1:18080", "DOC_ALERT_SPOOL_DIR": directory}
            with mock.patch.dict(os.environ, values):
                with self.assertRaises(SystemExit):
                    notifier["main"]()
            source = (ROOT / "scripts/hermes-native.sh").read_text()
            check = source.split('python3 - "$HERMES_HOME/.env" <<\'PY\'\n', 1)[1].split('\nPY\n', 1)[0]
            config = pathlib.Path(directory) / ".env"
            config.write_text("SIGNAL_ACCOUNT=+15551234567\nSIGNAL_HOME_CHANNEL=+15551234567\n"
                              "export SIGNAL_HOME_CHANNEL=+15557654321\nSIGNAL_HTTP_URL=http://127.0.0.1:18080\n")
            result = subprocess.run([sys.executable, "-c", check, str(config)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"one self-only Signal account", result.stderr)
            config.write_text("SIGNAL_ACCOUNT=+15551234567 # linked account\n"
                              "export SIGNAL_HOME_CHANNEL=+15551234567\t# Note to Self\n"
                              "SIGNAL_HTTP_URL=http://127.0.0.1:18080 # local daemon\n")
            result = subprocess.run([sys.executable, "-c", check, str(config)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_loaded_timer_is_unloaded_before_reconfiguration(self):
        source = (ROOT / "scripts/hermes-native.sh").read_text()
        start = source.split('  doc-alert-start)\n', 1)[1].split('  doc-alert-stop)\n', 1)[0]
        self.assertLess(start.index('launchctl bootout'), start.index('launchctl bootstrap'))
        self.assertIn('wait_unloaded "$DOC_ALERT_LABEL"', start)
        install = source.split('install_native() {\n', 1)[1].split('prepare_bridge_support_sync', 1)[0]
        self.assertIn('service_loaded "$DOC_ALERT_LABEL"', install)

    def test_daemon_failures_stop_after_three_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "doc-open-questions-8.pending").touch()
            for _ in range(3):
                notify(root, lambda text: self.fail("send must not run"),
                       lambda: (_ for _ in ()).throw(ConnectionError()))
            self.assertTrue((root / "doc-open-questions-8.failed").exists())


if __name__ == "__main__":
    unittest.main()
