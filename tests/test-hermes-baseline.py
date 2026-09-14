#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hermes-baseline.py"


class BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.container = f"hermes-baseline-{uuid.uuid4().hex[:10]}"
        subprocess.run(
            [
                "docker", "run", "-d", "--rm", "--name", cls.container,
                "-e", "POSTGRES_USER=fleet", "-e", "POSTGRES_PASSWORD=test",
                "-e", "POSTGRES_DB=fleet", "-p", f"127.0.0.1:{cls.port}:5432", "postgres:16",
            ],
            check=True,
            capture_output=True,
        )
        cls.addClassCleanup(
            subprocess.run, ["docker", "rm", "-f", cls.container], capture_output=True
        )
        for _ in range(30):
            ready = subprocess.run(
                ["docker", "exec", cls.container, "pg_isready", "-U", "fleet", "-d", "fleet"],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("postgres did not become ready")

        for migration in (
            "docker/initdb/01-schema.sql",
            "docker/initdb/02-agent-server.sql",
            "docker/initdb/03-human-review-queue.sql",
            "docker/initdb/04-pending-decision-approval.sql",
        ):
            cls.psql((ROOT / migration).read_text())
        cls.end = datetime.now(timezone.utc).replace(microsecond=123456) - timedelta(minutes=2)
        cls.start = cls.end - timedelta(hours=1)
        cls.psql(cls.fixture_sql())
        cls.psql(
            "CREATE ROLE baseline_reader LOGIN PASSWORD 'reader';"
            "GRANT CONNECT ON DATABASE fleet TO baseline_reader;"
            "GRANT USAGE ON SCHEMA public TO baseline_reader;"
            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO baseline_reader;"
        )

    @classmethod
    def psql(cls, sql):
        subprocess.run(
            ["docker", "exec", "-i", cls.container, "psql", "-v", "ON_ERROR_STOP=1", "-U", "fleet", "-d", "fleet"],
            input=sql,
            text=True,
            check=True,
            capture_output=True,
        )

    @classmethod
    def at(cls, minutes):
        return (cls.start + timedelta(minutes=minutes)).isoformat()

    @classmethod
    def fixture_sql(cls):
        values = {name: cls.at(minutes) for name, minutes in {
            "pre_created": -10, "pre_started": -9, "pre_finished": -5,
            "done_created": 0, "done_started": 1, "done_finished": 5,
            "failed_created": 10, "failed_started": 10.5, "failed_finished": 12,
            "reconcile_created": 20, "reconcile_started": 21, "reconcile_finished": 30,
            "queued_created": 40, "superseded_created": 45, "superseded_finished": 46,
            "spill_created": -60, "spill_started": 2, "spill_finished": 4,
            "after_created": 50, "after_started": 60.5, "after_finished": 61,
            "skipped_created": 35, "skipped_started": 35.5, "skipped_finished": 36,
            "edge_created": 55, "edge_started": 56, "edge_finished": 60,
            "pending_created": -30, "reviewed_created": 5, "reviewed_at": 6,
            "dismissed_created": 15, "dismissed_at": 16, "late_dismissed_created": 20,
            "late_dismissed_at": 61, "publishing_created": 30, "approved_created": 10,
            "approved_at": 15, "rejected_created": 25, "rejected_at": 26,
            "late_rejected_created": 20, "late_rejected_at": 61,
        }.items()}
        return f"""
INSERT INTO requests(kind,payload,dedupe_key,status,created_at,started_at,finished_at) VALUES
('pr-review','{{"canary":"payload-secret"}}','canary-dedupe','done','{values['pre_created']}','{values['pre_started']}','{values['pre_finished']}'),
('pr-review','{{}}','canary-dedupe','done','{values['done_created']}','{values['done_started']}','{values['done_finished']}'),
('pr-review','{{}}','canary-dedupe','failed','{values['failed_created']}','{values['failed_started']}','{values['failed_finished']}'),
('pr-maintain','{{}}','maintain','reconcile','{values['reconcile_created']}','{values['reconcile_started']}','{values['reconcile_finished']}'),
('doc-write','{{}}','queued','queued','{values['queued_created']}',NULL,NULL),
('pr-review','{{}}','superseded','superseded','{values['superseded_created']}',NULL,'{values['superseded_finished']}'),
('pr-review','{{}}','spill','done','{values['spill_created']}','{values['spill_started']}','{values['spill_finished']}'),
('doc-write','{{}}','after','done','{values['after_created']}','{values['after_started']}','{values['after_finished']}'),
('doc-write','{{}}','skipped','skipped','{values['skipped_created']}','{values['skipped_started']}','{values['skipped_finished']}'),
('doc-write','{{}}','edge','done','{values['edge_created']}','{values['edge_started']}','{values['edge_finished']}');

INSERT INTO pending_maintenance_reviews(request_id,proposal,provenance,state,created_at,reviewed_at) VALUES
(1,'{{}}','{{}}','pending','{values['pending_created']}',NULL),
(2,'{{}}','{{}}','reviewed','{values['reviewed_created']}','{values['reviewed_at']}'),
(3,'{{}}','{{}}','dismissed','{values['dismissed_created']}','{values['dismissed_at']}'),
(4,'{{}}','{{}}','dismissed','{values['late_dismissed_created']}','{values['late_dismissed_at']}');

INSERT INTO pending_decisions(request_id,kind,proposal,provenance,state,created_at,decided_at) VALUES
(5,'x','{{}}','{{}}','publishing','{values['publishing_created']}',NULL),
(6,'x','{{}}','{{}}','approved','{values['approved_created']}','{values['approved_at']}'),
(7,'x','{{}}','{{}}','rejected','{values['rejected_created']}','{values['rejected_at']}'),
(8,'x','{{}}','{{}}','rejected','{values['late_rejected_created']}','{values['late_rejected_at']}');
"""

    @staticmethod
    def iso(value):
        return value.isoformat().replace("+00:00", "Z")

    def environment(self):
        return os.environ | {
            "REQUESTS_DB_HOST": "127.0.0.1",
            "REQUESTS_DB_PORT": str(self.port),
            "REQUESTS_DB_USER": "baseline_reader",
            "REQUESTS_DB_NAME": "fleet",
            "PGPASSWORD": "reader",
        }

    def run_baseline(self, start=None, end=None, check=True, environment=None):
        result = subprocess.run(
            [str(SCRIPT), "--start", self.iso(start or self.start), "--end", self.iso(end or self.end)],
            env=environment or self.environment(),
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(result.stderr)
        return result

    def test_metrics_boundaries_and_dispositions(self):
        result = json.loads(self.run_baseline().stdout)

        self.assertEqual(result["window"]["start"], self.iso(self.start))
        self.assertEqual(result["window"]["end"], self.iso(self.end))
        self.assertEqual(result["requests"]["throughput"], {
            "count": 6, "denominator_seconds": 3600.0, "per_hour": 6.0,
        })
        self.assertEqual(result["requests"]["retry"], {"count": 1, "denominator": 8, "rate": 0.125})
        self.assertEqual(result["requests"]["failure"], {"count": 1, "denominator": 6, "rate": 0.166667})
        self.assertEqual(result["requests"]["reconcile"], {"count": 1, "denominator": 6, "rate": 0.166667})
        self.assertEqual(result["requests"]["superseded"], {"count": 1, "denominator": 6, "rate": 0.166667})
        self.assertEqual(result["requests"]["queue_age_seconds"], {"p50": 1200.0, "p95": 1200.0, "p99": 1200.0})
        self.assertEqual(result["requests"]["start_lag_seconds"], {"p50": 60.0, "p95": 3720.0, "p99": 3720.0})
        self.assertEqual(result["requests"]["completion_seconds"], {"p50": 120.0, "p95": 3840.0, "p99": 3840.0})
        self.assertEqual(result["requests"]["by_kind_status"], [
            {"count": 2, "kind": "doc-write", "status": "done"},
            {"count": 1, "kind": "doc-write", "status": "queued"},
            {"count": 1, "kind": "doc-write", "status": "skipped"},
            {"count": 1, "kind": "pr-maintain", "status": "reconcile"},
            {"count": 1, "kind": "pr-review", "status": "done"},
            {"count": 1, "kind": "pr-review", "status": "failed"},
            {"count": 1, "kind": "pr-review", "status": "superseded"},
        ])
        self.assertEqual(result["human"]["by_queue_state"], [
            {"count": 1, "queue": "decision", "state": "approved"},
            {"count": 1, "queue": "decision", "state": "publishing"},
            {"count": 1, "queue": "decision", "state": "rejected"},
            {"count": 1, "queue": "maintenance", "state": "dismissed"},
            {"count": 1, "queue": "maintenance", "state": "pending"},
            {"count": 1, "queue": "maintenance", "state": "reviewed"},
        ])
        self.assertEqual(result["human"]["pending_count"], 2)
        self.assertEqual(result["human"]["oldest_pending_age_seconds"], 5400.0)
        self.assertEqual(result["target_audit"]["duplicate_effects"], {"count": None, "status": "unavailable"})
        self.assertEqual(result["target_audit"]["missed_eligible"], {"count": None, "status": "unavailable"})

    def test_output_is_byte_stable_and_excludes_row_content(self):
        first = self.run_baseline().stdout
        second = self.run_baseline().stdout
        self.assertEqual(first, second)
        self.assertNotIn("payload-secret", first)
        self.assertNotIn("canary-dedupe", first)

    def test_empty_window_is_valid(self):
        start = self.start - timedelta(days=1)
        end = datetime.now(timezone.utc).replace(microsecond=0)
        self.psql("TRUNCATE pending_maintenance_reviews, pending_decisions, requests RESTART IDENTITY CASCADE;")
        try:
            result = json.loads(self.run_baseline(start, end).stdout)
            self.assertEqual(result["requests"]["throughput"]["count"], 0)
            for name in ("queue_age_seconds", "start_lag_seconds", "completion_seconds"):
                self.assertEqual(result["requests"][name], {"p50": None, "p95": None, "p99": None})
            for name in ("failure", "retry", "reconcile", "superseded"):
                self.assertEqual(result["requests"][name]["count"], 0)
                self.assertIsNone(result["requests"][name]["rate"])
            self.assertEqual(result["human"]["pending_count"], 0)
            self.assertIsNone(result["human"]["oldest_pending_age_seconds"])
        finally:
            self.psql(self.fixture_sql())

    def test_invalid_windows_fail_before_database_access(self):
        unusable = self.environment() | {"PSQL_BIN": "/no/such/psql"}
        for start, end in [
            (self.end, self.start),
            (self.start, self.start),
        ]:
            self.assertNotEqual(self.run_baseline(start, end, check=False, environment=unusable).returncode, 0)
        for start, end in [
            (self.iso(self.start).removesuffix("Z"), self.iso(self.end)),
            (self.iso(self.start), self.iso(self.end).removesuffix("Z")),
            ("bad", self.iso(self.end)),
        ]:
            result = subprocess.run(
                [str(SCRIPT), "--start", start, "--end", end], env=unusable,
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_historical_end_is_rejected_before_database_access(self):
        unusable = self.environment() | {"PSQL_BIN": "/no/such/psql"}
        result = self.run_baseline(
            self.start - timedelta(days=1), self.end - timedelta(minutes=6),
            check=False, environment=unusable,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("within five minutes", result.stderr)
        future = datetime.now(timezone.utc) + timedelta(minutes=1)
        result = self.run_baseline(self.start, future, check=False, environment=unusable)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not in the future", result.stderr)


if __name__ == "__main__":
    unittest.main()
