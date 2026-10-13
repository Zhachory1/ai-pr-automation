#!/usr/bin/env python3
"""Keep the retired request database out of runnable fleet code."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(
    r"REQUESTS_DB_|PGPASSWORD|db-requests|requests_pgdata|postgresql-client|"
    r"\bpsql\b|hermes_(?:claim|enqueue|renew|settle)_request|lib/queue\.sh"
)


class RequestDbRetiredTest(unittest.TestCase):
    def test_no_request_database_runtime_or_migrations(self):
        for path in (
            "scripts/hermes-native.sh", "scripts/hermes-controller.py", "lib/queue.sh",
            "bin/status-server", "bin/hermes-queue-runner", "Dockerfile.hermes-controller",
            "Dockerfile.status", "docker-compose.status-rollback.yml",
            "bin/hermes-kanban-safety-bridge", "bin/hermes-doc-alert-notify",
        ):
            with self.subTest(path=path):
                self.assertFalse((ROOT / path).exists())
        self.assertFalse(list((ROOT / "docker/initdb").glob("*.sql")))

    def test_no_request_database_dependency_in_runnable_code(self):
        paths = [ROOT / "docker-compose.yml", *ROOT.glob("Dockerfile.*")]
        for directory in ("bin", "scripts", "launchd", "agent-config/hermes/profiles"):
            paths.extend(path for path in (ROOT / directory).rglob("*")
                         if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts)
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIsNone(FORBIDDEN.search(path.read_text(encoding="utf-8")))

    def test_hindsight_database_is_independent(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("  hindsight-db:", compose)
        self.assertNotIn("  db-requests:", compose)


if __name__ == "__main__":
    unittest.main()
