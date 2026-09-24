#!/usr/bin/env python3
import hashlib
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hermes_pr_safety_result as result


PAYLOAD = {
    "operation_id": "pr-safety-fixture",
    "repo": "ROKT/example",
    "pr": 7,
    "head_sha": "1" * 40,
    "base_sha": "2" * 40,
    "diff_hash": "3" * 64,
    "policy_version": "v1",
    "policy_digest": "4" * 64,
}
NONCE = "a" * 32


def package(verdict="clear"):
    return {
        "workflow_id": "workflow",
        "artifact_digest": "5" * 64,
        "verdict": verdict,
        "intent": {"summary": "fixture"},
        "findings": [],
        "coverage": {},
        "documentation": {},
        "observability": {},
        "incident": {
            "candidate": False,
            "changed_line_cause": False,
            "concrete_trigger": False,
            "severe_impact": False,
            "high_confidence_chain": False,
            "stop_rollback_or_page": False,
            "evidence": [],
        },
        "human_decisions_needed": [],
        "dissent": [],
        "residual_risk": [],
    }


class SafetyResultTest(unittest.TestCase):
    def test_exact_clear_mapping(self):
        mapped = result.map_council_safety(package(), PAYLOAD, NONCE)
        self.assertEqual(mapped, {
            "nonce": NONCE,
            **PAYLOAD,
            "status": "clear",
            "intent": {"summary": "fixture"},
            "findings": [],
            "coverage": {"council": {
                "workflow_id": "workflow", "artifact_digest": "5" * 64,
                "dissent": [], "residual_risk": [],
            }},
            "documentation": {},
            "observability": {},
            "incident": {
                "candidate": False, "changed_line_cause": False, "concrete_trigger": False,
                "severe_impact": False, "high_confidence_chain": False,
                "stop_rollback_or_page": False, "evidence": [],
            },
            "human_decisions_needed": [],
        })
        self.assertTrue(result.valid_safety(mapped, PAYLOAD, NONCE))

    def test_exact_inconclusive_and_incident_mapping(self):
        inconclusive = result.map_council_safety(package("inconclusive"), PAYLOAD, NONCE)
        self.assertEqual(inconclusive["status"], "needs_human_decision")

        incident = package("incident_candidate")
        incident["incident"] = {
            "candidate": True,
            "changed_line_cause": True,
            "concrete_trigger": True,
            "severe_impact": True,
            "high_confidence_chain": True,
            "stop_rollback_or_page": True,
            "evidence": [{"path": "app.py", "line": 7, "side": "new", "quote": "drop table"}],
        }
        mapped = result.map_council_safety(incident, PAYLOAD, NONCE)
        self.assertEqual(mapped["status"], "incident_candidate")
        self.assertTrue(mapped["incident"]["candidate"])
        self.assertTrue(result.valid_safety(mapped, PAYLOAD, NONCE))

    def test_exact_handoff_bytes_digest_and_publication_replay(self):
        mapped = result.map_council_safety(package("needs_human_decision"), PAYLOAD, NONCE)
        expected = (
            "<!-- pr-safety identity\n"
            "operation_id: pr-safety-fixture\nrepo: ROKT/example\npr: 7\n"
            f"head_sha: {'1' * 40}\nbase_sha: {'2' * 40}\ndiff_hash: {'3' * 64}\n"
            "policy_version: v1\nstatus: needs_human_decision\nincident_candidate: false\n-->\n\n"
            "## Concrete breakage\n\n```json\n[]\n```\n\n"
            "## Human decisions\n\n```json\n"
            "{\"intent\":{\"summary\":\"fixture\"},\"items\":[]}\n```\n\n"
            "## Council context\n\n```json\n"
            f"{{\"artifact_digest\":\"{'5' * 64}\",\"dissent\":[],\"residual_risk\":[],"
            "\"workflow_id\":\"workflow\"}\n```\n"
        ).encode()
        self.assertEqual(result.render_safety_handoff(PAYLOAD, mapped), expected)
        expected_digest = hashlib.sha256(expected).hexdigest()
        self.assertEqual(expected_digest, "00b56eaa77154f150adfdf353bee5b5f0bf0b689c9055d1ad05df3bbfad9e112")

        with tempfile.TemporaryDirectory() as td:
            path, digest = result.publish_safety_handoff(td, PAYLOAD, mapped)
            self.assertEqual(pathlib.Path(path).read_bytes(), expected)
            self.assertEqual(digest, expected_digest)
            self.assertEqual(pathlib.Path(path).stat().st_mode & 0o777, 0o640)
            self.assertEqual(result.publish_safety_handoff(td, PAYLOAD, mapped), (path, digest))
            pathlib.Path(path).write_text("different")
            with self.assertRaisesRegex(ValueError, "differs"):
                result.publish_safety_handoff(td, PAYLOAD, mapped)

    def test_handoff_rejects_path_escape_and_symlinks(self):
        mapped = result.map_council_safety(package("needs_human_decision"), PAYLOAD, NONCE)
        with tempfile.TemporaryDirectory() as td:
            valid_hidden = dict(PAYLOAD, repo="ROKT/.github")
            path, _ = result.publish_safety_handoff(td, valid_hidden, mapped)
            self.assertEqual(pathlib.Path(path).name, "ROKT__.github__pr7__pr-safety-fixture.md")
            for changed in (
                dict(PAYLOAD, operation_id="../escape"),
                dict(PAYLOAD, repo="ROKT/../../escape"),
                dict(PAYLOAD, pr="../escape"),
            ):
                with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "identity"):
                    result.publish_safety_handoff(td, changed, mapped)

            target = pathlib.Path(td) / "ROKT__example__pr7__pr-safety-fixture.md"
            outside = pathlib.Path(td).parent / "outside-handoff"
            target.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "differs"):
                result.publish_safety_handoff(td, PAYLOAD, mapped)

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "root"
            destination = pathlib.Path(td) / "destination"
            destination.mkdir(); root.symlink_to(destination, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "unsafe safety handoff root"):
                result.publish_safety_handoff(root, PAYLOAD, mapped)


if __name__ == "__main__":
    unittest.main()
