#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "agent-config/skills/design-workflow/SKILL.md"


class DesignWorkflowSkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.text = SKILL.read_text()

    def test_identity_and_dynamic_shape(self):
        self.assertIn("name: design-workflow", self.text)
        self.assertIn("design-write:{operation}:{round}:{role}", self.text)
        self.assertIn("Create one round-0 writer", self.text)
        self.assertIn("Never create round 3", self.text)
        self.assertIn("Maximum automatic revision rounds are 1 and 2", self.text)

    def test_design_sections(self):
        for text in ("current architecture", "goals and non-goals", "proposed architecture and boundaries", "APIs, events, schemas", "data flow, storage, lineage, and state ownership", "migration, rollout, rollback", "alternatives and explicit trade-offs", "testing, validation, observability", "Mermaid topology/sequence diagrams"):
            self.assertIn(text, self.text)

    def test_required_reviewers(self):
        self.assertIn("`software-architect` → `software-architect`", self.text)
        self.assertIn("`mvp` → `mvp`", self.text)
        self.assertIn("`occams-razor` → `occams-razor`", self.text)
        self.assertIn("All rounds include Software Architect, MVP, and Occam", self.text)

    def test_evidence_and_read_only_boundary(self):
        self.assertIn("`ROKT/ads-success-kb` and `ROKT/zhach-private-docs`", self.text)
        self.assertIn("recall/reflect only, never retain", self.text)
        self.assertIn("Never invoke create, update, publish, comment, transition, retry, cancel, unblock, deploy, retain", self.text)
        self.assertIn("OWNER/REPO@SHA:path:line", self.text)
        self.assertIn("Accessible facts deferred as assumptions return `revise`", self.text)

    def test_artifact_and_human_authority(self):
        self.assertIn("require `verified=true`", self.text)
        self.assertIn("Compute SHA-256", self.text)
        self.assertIn("Use `kanban_complete.artifacts`", self.text)
        self.assertIn("human_decision_v1", self.text)
        self.assertIn("Main never completes another worker task", self.text)
        self.assertIn("does not authorize implementation, migration, publication, deployment, merge, release, or archival", self.text)

    def test_no_new_infrastructure(self):
        self.assertIn("No custom workflow MCP", self.text)
        self.assertIn("Worker cards do not force-load skills", self.text)
        self.assertNotIn("delegate_task", self.text)


if __name__ == "__main__": unittest.main()
