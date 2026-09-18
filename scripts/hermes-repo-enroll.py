#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message):
    raise SystemExit(f"Hermes repository enrollment: {message}")


def gate(path):
    result = subprocess.run([str(ROOT / "scripts/hermes-repo-gate.py"), str(path)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        fail(result.stderr.strip() or "gate failed")
    return json.loads(result.stdout)


def psql(variables, sql):
    command = ["psql", "-v", "ON_ERROR_STOP=1", "-qAt", "-h", os.environ.get("REQUESTS_DB_HOST", "localhost"),
               "-p", os.environ.get("REQUESTS_DB_PORT", "5432"), "-U", os.environ.get("REQUESTS_DB_USER", "fleet"),
               "-d", os.environ.get("REQUESTS_DB_NAME", "fleet")]
    for key, value in variables.items():
        command += ["-v", f"{key}={value}"]
    result = subprocess.run(command, input=sql, capture_output=True, text=True, timeout=30)
    if result.returncode:
        fail(result.stderr.strip() or "database update failed")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    value = gate(args.evidence)
    variables = {
        "repo": value["repo"], "credential": value["credential_fingerprint"],
        "ruleset": value["ruleset_digest"], "workflow": value["workflow_digest"],
        "environment": value["environment_policy_digest"], "proof": value["proof_digest"],
        "checked": value["checked_at"],
    }
    proof = psql(variables, """
INSERT INTO hermes_repository_enrollments(
  repo,credential_fingerprint,ruleset_digest,workflow_digest,environment_policy_digest,
  proof_digest,checked_at,approved_at,active,invalid_reason)
VALUES(:'repo',:'credential',:'ruleset',:'workflow',:'environment',:'proof',:'checked'::timestamptz,
       clock_timestamp(),true,NULL)
ON CONFLICT(repo) DO UPDATE SET
  credential_fingerprint=excluded.credential_fingerprint,ruleset_digest=excluded.ruleset_digest,
  workflow_digest=excluded.workflow_digest,environment_policy_digest=excluded.environment_policy_digest,
  proof_digest=excluded.proof_digest,checked_at=excluded.checked_at,approved_at=clock_timestamp(),
  active=true,invalid_reason=NULL
RETURNING proof_digest;
""")
    if proof != value["proof_digest"]:
        fail("database did not confirm proof")
    print(json.dumps({"status": "enrolled", "repo": value["repo"], "proof_digest": proof},
                     sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
