#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "agent-config/skills/hermes-queue-client"
SOURCES = (
    "scripts/hermes-queue-client.py",
    "scripts/hermes_direct_pr_journal.py",
    "scripts/hermes_run_request.py",
    "scripts/hermes-authority.py",
    "scripts/hermes-prd-kanban-enqueue.py",
    "scripts/hermes-repository-cache.py",
    "bin/hermes-git-read-askpass",
)


class SkillBundleTest(unittest.TestCase):
    def test_skill_contains_current_client_and_helpers(self):
        for name in SOURCES:
            with self.subTest(name=name):
                data = (SKILL / name).read_bytes()
                if name == "scripts/hermes-queue-client.py":
                    guard = b'    if value["kind"] == "pr-safety": fail("pr-safety is not supported by this skill bundle")\n'
                    self.assertEqual(data.count(guard), 1)
                    data = data.replace(guard, b"")
                self.assertEqual(data, (ROOT / name).read_bytes())
        self.assertFalse((SKILL / "bin/hermes-pr-safety-producer").exists())
        instructions = (SKILL / "SKILL.md").read_text()
        self.assertNotIn("FLEET_REPO", instructions)
        self.assertIn("$HOME/.local/share/ai-pr-automation/hermes-queue-client", instructions)
        self.assertIn("scripts/queue", instructions)

    def test_document_writer_uses_bundled_helpers_outside_checkout(self):
        fake_spec = importlib.util.spec_from_file_location("source_test", ROOT / "tests/test-hermes-prd-kanban-enqueue.py")
        source_test = importlib.util.module_from_spec(fake_spec)
        fake_spec.loader.exec_module(source_test)
        with tempfile.TemporaryDirectory() as directory:
            relocated = Path(directory) / "hermes-queue-client"
            shutil.copytree(SKILL, relocated)
            spec = importlib.util.spec_from_file_location("bundled_enqueue", relocated / "scripts/hermes-prd-kanban-enqueue.py")
            enqueue = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(enqueue)
            args = argparse.Namespace(hermes_home=Path(directory) / "home/.hermes", hermes_bin=Path(directory) / "hermes",
                                      engine="dynamic", document_kind="prd", repository_cache_root=Path(directory) / "cache",
                                      knowledge_repositories=[])
            core = {"title": "Sample", "requester": "local-agent", "requirements": "Goals", "repositories": ["OWNER/repo"]}
            payload = {"operation_id": "prd-" + hashlib.sha256(enqueue.canonical(core)).hexdigest(), **core}
            cli = source_test.FakeCli()
            with patch.object(enqueue, "run", side_effect=cli), patch("sys.stdin", io.StringIO(enqueue.canonical(payload).decode())):
                result = enqueue.enqueue(args)
            body = json.loads(cli.tasks[result["tasks"]["writer"]]["body"])
            rules = " ".join(body["repository_rules"])
            for name in ("scripts/hermes-authority.py", "scripts/hermes-repository-cache.py", "bin/hermes-git-read-askpass"):
                self.assertIn(str((relocated / name).resolve()), rules)
                self.assertTrue((relocated / name).is_file())

    def test_client_imports_and_validates_intake_outside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            relocated = Path(directory) / "hermes-queue-client"
            shutil.copytree(SKILL, relocated)
            code = ("import importlib.util, json, sys; from pathlib import Path; "
                    "root=Path(sys.argv[1]); sys.path.insert(0, str(root/'scripts')); "
                    "spec=importlib.util.spec_from_file_location('client', root/'scripts/hermes-queue-client.py'); "
                    "client=importlib.util.module_from_spec(spec); spec.loader.exec_module(client); "
                    "assert client.ROOT==(root/'scripts').resolve(); "
                    "assert client.intake(b'{\"version\":1,\"kind\":\"pr-maintain\",\"repository\":\"OWNER/repo\",\"pr\":123}')['kind']=='pr-maintain'; "
                    "\ntry:\n client.intake(b'{\"version\":1,\"kind\":\"pr-safety\"}')\n"
                    "except ValueError as error:\n assert 'not supported' in str(error)\n"
                    "else:\n raise AssertionError('pr-safety accepted')\n"
                    "print('bundle works')")
            result = subprocess.run([sys.executable, "-c", code, str(relocated)],
                                    capture_output=True, text=True, cwd=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "bundle works")
            help_result = subprocess.run([str(relocated / "scripts/queue"), "--help"],
                                         capture_output=True, text=True, cwd=directory)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("status-check", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
