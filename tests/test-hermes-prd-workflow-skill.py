#!/usr/bin/env python3
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "agent-config/skills/prd-workflow/SKILL.md"


class PrdWorkflowSkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text()

    def test_skill_identity_and_native_tools(self):
        self.assertIn("name: prd-workflow", self.text)
        for tool in ("kanban_create", "kanban_list", "kanban_show", "kanban_comment", "kanban_unblock", "kanban_complete", "kanban_block", "write_file", "read_file", "execute_code"):
            self.assertIn(f"`{tool}`", self.text)
        self.assertIn("Do not use `kanban_attach` for generated text", self.text)
        self.assertIn("Do not call a custom workflow MCP", self.text)

    def test_intake_creates_only_writer(self):
        intake = self.text.split("## Intake", 1)[1].split("## Writer Stage", 1)[0]
        self.assertIn("Create only the round-0 writer task", intake)
        self.assertIn("`goal_mode=true`, `goal_max_turns=4`, and `max_runtime_seconds=3600`", intake)
        self.assertIn("sorted compact UTF-8 JSON", intake)
        self.assertIn('"prd-" + sha256(canonical_bytes)', intake)
        self.assertIn("Resolve every explicitly named `OWNER/REPO`", intake)
        self.assertIn("Do not create reviewers or synthesis at intake", intake)

    def test_writer_creates_required_round_graph(self):
        writer = self.text.split("## Writer Stage", 1)[1].split("## Reviewer Stage", 1)[0]
        self.assertIn("current writer task as parent", writer)
        self.assertIn("current writer and every reviewer as parents", writer)
        self.assertIn("use `search_files` for discovery and `read_file` for evidence", writer)
        self.assertIn("OWNER/REPO@SHA:path:line", writer)
        self.assertIn("Require `verified=true`", writer)
        self.assertIn("Python `hashlib.sha256`", writer)
        self.assertIn("absolute Markdown path in `artifacts`", writer)
        self.assertIn("exact `created_cards` list", writer)
        for role in ("product-pm", "mvp", "occams-razor"):
            self.assertIn(f"`{role}`", writer)
        self.assertIn("Assign profiles exactly", writer)
        self.assertIn("Assign `prd-write-v1`, do not set task `skills`", writer)

    def test_synthesis_creates_only_next_writer(self):
        synthesis = self.text.split("## Synthesis Stage", 1)[1].split("## Human Decision", 1)[0]
        self.assertIn("Create only one next-round writer task with `goal_mode=true`", synthesis)
        self.assertIn("Next writer creates its own council and synthesis", synthesis)
        self.assertIn("Never create round 3", synthesis)
        self.assertIn("kind=needs_input", synthesis)
        self.assertIn("Assign `prd-write-v1`, do not set task `skills`", synthesis)

    def test_idempotency_and_revision_contract(self):
        self.assertRegex(self.text, re.escape("prd-write:{operation}:{round}:{role}"))
        self.assertIn("Every review round includes `mvp` and `occams-razor`", self.text)
        self.assertIn("Maximum automatic revision rounds are 1 and 2 after round 0", self.text)
        self.assertIn("Never silently reopen failed or denied work", self.text)
        self.assertIn("newest decision comment author is exactly `default`", self.text)
        self.assertIn("comment follows this task's latest `needs_input` block", self.text)
        self.assertIn("Never call `kanban_complete` on another worker's task", self.text)
        self.assertIn("Invalid, stale, mismatched, or non-`default` comments are not authority", self.text)
        self.assertIn("placeholder digest", self.text)
        self.assertIn("unreadable attachment is malformed evidence", self.text)
        self.assertIn("task bodies are the durable worker interface", self.text)
        self.assertIn("Reviewers and synthesis remain single-shot", self.text)
        self.assertIn("snapshot_sha == head_sha", self.text)
        self.assertIn("`ROKT/ads-success-kb` and `ROKT/zhach-private-docs`", self.text)
        self.assertIn("recall and reflect only, never retain", self.text)
        self.assertIn("Never invoke create, update, publish, comment, transition, retry, cancel, unblock, deploy, retain", self.text)
        self.assertIn("substitutes assumptions or future discovery for readable repository facts must return `revise`", self.text)

    def test_profile_ownership_boundary(self):
        self.assertIn(
            "Do not inspect or pin profile files, tools, MCPs, defaults, or digests",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
