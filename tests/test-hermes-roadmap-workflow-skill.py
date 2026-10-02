#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "agent-config/skills/roadmap-workflow/SKILL.md"


class RoadmapWorkflowSkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.text = SKILL.read_text()

    def test_identity_and_dynamic_shape(self):
        self.assertIn("name: roadmap-workflow", self.text)
        self.assertIn("roadmap-write:{operation}:{round}:{role}", self.text)
        self.assertIn("Create one goal-mode `roadmap-write-v1` writer", self.text)
        self.assertIn("Never create round 3", self.text)
        self.assertIn("Maximum automatic revisions are rounds 1 and 2", self.text)

    def test_roadmap_sections(self):
        for text in ("planning horizon and decision date", "goals, outcomes, and measurable success", "candidate initiatives and explicit exclusions", "prioritization method and score rationale", "now/next/later sequencing", "dependencies and critical path", "capacity and staffing assumptions", "milestones, launch gates, and decision points", "rollback and de-scope options", "measurement owner, and review cadence", "Mermaid dependency/timeline diagrams"):
            self.assertIn(text, self.text)

    def test_required_reviewers(self):
        self.assertIn("`product-pm` → `product-pm`", self.text)
        self.assertIn("`vp-eng` → `vp-eng`", self.text)
        self.assertIn("`mvp` → `mvp`", self.text)
        self.assertIn("`occams-razor` → `occams-razor`", self.text)
        self.assertIn("All four reviewers run every round", self.text)

    def test_evidence_and_read_only_boundary(self):
        self.assertIn("`ROKT/ads-success-kb` and `ROKT/zhach-private-docs`", self.text)
        self.assertIn("recall/reflect only, never retain", self.text)
        self.assertIn("Never invoke create, update, publish, comment, transition, retry, cancel, unblock, deploy, retain", self.text)
        self.assertIn("Do not fabricate precision", self.text)
        self.assertIn("Accessible facts deferred as discovery return `revise`", self.text)
        self.assertIn("Prepare and pin Repository Evidence before drafting", self.text)
        self.assertIn("enroll missing repositories, sync stale manifests, and materialize missing snapshots", self.text)

    def test_human_authority_and_non_commitment(self):
        self.assertIn("human_decision_v1", self.text)
        self.assertIn("it never completes another worker task", self.text)
        self.assertIn("Approval authorizes the roadmap document only", self.text)
        self.assertIn("not staffing, budget, dates, launches, publication, or execution", self.text)

    def test_reuses_native_contract(self):
        self.assertIn("Use `kanban_complete.artifacts`", self.text)
        self.assertIn("Worker cards do not force-load skills", self.text)
        self.assertIn("No custom workflow MCP", self.text)
        self.assertNotIn("delegate_task", self.text)


if __name__ == "__main__": unittest.main()
