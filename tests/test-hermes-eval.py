#!/usr/bin/env python3
import copy
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hermes_eval", ROOT / "scripts/hermes-eval.py")
hermes_eval = importlib.util.module_from_spec(spec); spec.loader.exec_module(hermes_eval)
MANIFEST = json.loads((ROOT / "evals/manifest.json").read_text())


class HermesEvalContractTest(unittest.TestCase):
    def changed(self): return copy.deepcopy(MANIFEST)

    def assert_invalid(self, data, text):
        with self.assertRaisesRegex(ValueError, text): hermes_eval.validate(data)

    def test_repository_manifest_and_cli_validate(self):
        self.assertEqual(hermes_eval.validate(MANIFEST),
                         {"schema_version":1,"profiles":6,"cases":0,"hard_gates":10})
        result = subprocess.run([sys.executable, str(ROOT / "scripts/hermes-eval.py"), "validate",
            str(ROOT / "evals/manifest.json")], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout),
                         {"schema_version":1,"profiles":6,"cases":0,"hard_gates":10})

    def test_unknown_profile_fails(self):
        data = self.changed(); data["profiles"]["unknown-v1"] = data["profiles"].pop("doc-write-v1")
        self.assert_invalid(data, "profile set changed")

    def test_nonzero_or_noninteger_hard_gate_fails(self):
        for value in (1, False, 0.0):
            data = self.changed(); data["hard_gates"]["unauthorized_writes"] = value
            self.assert_invalid(data, "integer zero")

    def test_invalid_changed_or_duplicate_metric_fails(self):
        data = self.changed(); data["profiles"]["pr-review-v1"]["primary_metric"]["minimum"] = 1.1
        self.assert_invalid(data, "number from 0 to 1")
        data = self.changed(); data["profiles"]["pr-review-v1"]["primary_metric"]["minimum"] = .81
        self.assert_invalid(data, "metric contract changed")
        data = self.changed(); profile = data["profiles"]["pr-review-v1"]
        profile["quality_metrics"][0]["name"] = profile["primary_metric"]["name"]
        self.assert_invalid(data, "duplicate metric")

    def test_metric_cannot_have_minimum_and_maximum(self):
        data = self.changed(); data["profiles"]["pr-review-v1"]["quality_metrics"][0]["maximum"] = 1
        self.assert_invalid(data, "exactly one")

    def test_secret_like_value_fails(self):
        data = self.changed(); data["evaluator_version"] = "github_pat_abcdefghijklmnopqrstuvwxyz123456"
        self.assert_invalid(data, "secret-like value")

    def test_cases_are_unique_profile_scoped_and_bounded(self):
        case = {"id":"review-clean-v1","profile":"pr-review-v1",
                "path":"evals/cases/pr-review/review-clean-v1","repetitions":3}
        data = self.changed(); data["cases"] = [case]
        self.assertEqual(hermes_eval.validate(data)["cases"], 1)
        data["cases"].append(copy.deepcopy(case)); self.assert_invalid(data, "duplicate case id")
        data = self.changed(); data["cases"] = [dict(case, path="../private")]
        self.assert_invalid(data, "invalid case path")
        data = self.changed(); data["cases"] = [dict(case, repetitions=2)]
        self.assert_invalid(data, "invalid repetitions")
        data = self.changed(); data["cases"] = [dict(case, profile=7)]
        self.assert_invalid(data, "unknown profile")

    def test_cli_failure_is_concise(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "bad.json"; path.write_text("{}")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/hermes-eval.py"), "validate", str(path)],
                                    capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Hermes eval validation failed", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__": unittest.main()
