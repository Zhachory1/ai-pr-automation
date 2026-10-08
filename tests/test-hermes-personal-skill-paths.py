#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PersonalSkillPathsTest(unittest.TestCase):
    def test_document_workflows_use_operator_owned_repo_and_token_paths(self):
        for name in ("design-workflow", "prd-workflow", "roadmap-workflow"):
            with self.subTest(name=name):
                source = (ROOT / "agent-config/skills" / name / "SKILL.md").read_text()
                self.assertIn('"$AI_PR_AUTOMATION_ROOT/scripts/hermes-repository-cache.py"', source)
                self.assertIn('"$AI_PR_AUTOMATION_ROOT/scripts/hermes-authority.py"', source)
                self.assertIn('GITHUB_READ_TOKEN_FILE="$HERMES_HOME/secrets/github-read-token"', source)
                self.assertNotIn("/usr/local/libexec/ai-pr-automation/", source)
                self.assertNotIn("/Users/Shared/ai-pr-automation-runtime/secrets/", source)


if __name__ == "__main__":
    unittest.main()
