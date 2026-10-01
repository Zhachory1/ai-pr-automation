#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "agent-config/skills/swe-implement-workflow/SKILL.md"


class SweImplementWorkflowSkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.text = SKILL.read_text()

    def test_typed_identity_and_approval(self):
        self.assertIn("name: swe-implement-workflow", self.text)
        self.assertIn("human_approval: explicit approval record", self.text)
        self.assertIn("swe-implement:{operation}", self.text)
        self.assertIn("Models never invent operation IDs", self.text)
        self.assertIn("Do not infer approval", self.text)

    def test_single_repo_isolated_workspace(self):
        self.assertIn("one authorized repository", self.text)
        self.assertIn("one isolated Hermes worktree or fresh clone", self.text)
        self.assertIn("never use a dirty/shared checkout", self.text)
        self.assertIn("base SHA equal to request `base_sha`", self.text)

    def test_minimal_implementation_and_validation(self):
        self.assertIn("### Caveman reasoning", self.text)
        self.assertIn("facts\nconstraints\nunknowns\nsmallest safe plan\nproof", self.text)
        self.assertIn("Do not invent context, speculate past evidence, or expose private chain-of-thought", self.text)
        self.assertIn("### Ponytail coding", self.text)
        self.assertIn("Stop at the first rung that holds", self.text)
        self.assertIn("Deletion beats addition. Boring beats clever. Fewest files wins", self.text)
        self.assertIn("Make the smallest complete patch", self.text)
        self.assertIn("Run repository-required focused checks", self.text)
        self.assertIn("Do not claim unseen or truncated output", self.text)
        self.assertIn("conventional, specific subject", self.text)

    def test_effect_reconciliation(self):
        self.assertIn("Unknown push result", self.text)
        self.assertIn("Read remote branch", self.text)
        self.assertIn("matching branch/marker/operation", self.text)
        self.assertIn("different commit or unreadable state", self.text)
        self.assertIn("Read back PR URL, draft state, base, head branch/SHA, marker, repository, and author", self.text)

    def test_pr_review_handoff(self):
        self.assertIn("Enqueue exact repository, PR number, and head SHA on `pr-review`", self.text)
        self.assertIn("review task ID", self.text)
        self.assertIn("Do not merge or approve your own PR", self.text)

    def test_forbidden_authority(self):
        for text in ("default/protected branch push", "force-push", "merge, deploy, release, package publication", "credential output", "direct Kanban SQLite access", "skipping hooks", "multiple repositories", "`delegate_task`"):
            self.assertIn(text, self.text)

    def test_structured_completion(self):
        for field in ("acceptance", "validation", "draft_pr_url", "pr_head_sha", "review_task_id", "created_cards"):
            self.assertIn(field, self.text)


if __name__ == "__main__": unittest.main()
