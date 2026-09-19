#!/usr/bin/env python3
"""Enroll the reserved local/fleet sentinel that authorizes non-repo-scoped Hermes roles
(doc-write, memory-curate). It carries no GitHub capability claim: local roles touch no repository,
so the GitHub allowed/denial probes do not apply. The proof binds only the provider/profile
authority the operator vouches for, and the same 10-minute freshness and enrollment gate still apply.
"""
import argparse
import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SENTINEL = "local/fleet"


def fail(message):
    raise SystemExit(f"Hermes local enrollment: {message}")


def psql(variables, sql):
    command = ["psql", "-v", "ON_ERROR_STOP=1", "-qAt",
               "-h", os.environ.get("REQUESTS_DB_HOST", "localhost"),
               "-p", os.environ.get("REQUESTS_DB_PORT", "5432"),
               "-U", os.environ.get("REQUESTS_DB_USER", "fleet"),
               "-d", os.environ.get("REQUESTS_DB_NAME", "fleet")]
    for key, value in variables.items():
        command += ["-v", f"{key}={value}"]
    result = subprocess.run(command, input=sql, capture_output=True, text=True, timeout=30)
    if result.returncode:
        fail(result.stderr.strip() or "database update failed")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path, help="JSON: {schema_version:1, authority_digest:<64 hex>}")
    args = parser.parse_args()
    descriptor = os.open(args.evidence, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            fail("evidence must be a regular file")
        try:
            evidence = json.load(source)
        except json.JSONDecodeError:
            fail("evidence must be JSON")
    if (not isinstance(evidence, dict) or set(evidence) != {"schema_version", "authority_digest"}
            or evidence["schema_version"] != 1):
        fail("evidence shape changed")
    digest = evidence["authority_digest"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        fail("invalid authority digest")
    checked = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # A sentinel carries no GitHub authority; the four digest columns all bind the single local
    # authority digest so the row is well-formed and any change re-triggers the authority watcher.
    proof = hashlib.sha256(
        json.dumps({"repo": SENTINEL, "authority_digest": digest}, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest()
    variables = {"repo": SENTINEL, "digest": digest, "proof": proof, "checked": checked}
    confirmed = psql(variables, """
INSERT INTO hermes_repository_enrollments(
  repo,credential_fingerprint,ruleset_digest,workflow_digest,environment_policy_digest,
  proof_digest,checked_at,approved_at,active,invalid_reason)
VALUES(:'repo',:'digest',:'digest',:'digest',:'digest',:'proof',:'checked'::timestamptz,
       clock_timestamp(),true,NULL)
ON CONFLICT(repo) DO UPDATE SET
  credential_fingerprint=excluded.credential_fingerprint,ruleset_digest=excluded.ruleset_digest,
  workflow_digest=excluded.workflow_digest,environment_policy_digest=excluded.environment_policy_digest,
  proof_digest=excluded.proof_digest,checked_at=excluded.checked_at,approved_at=clock_timestamp(),
  active=true,invalid_reason=NULL
RETURNING proof_digest;
""")
    if confirmed != proof:
        fail("database did not confirm proof")
    print(json.dumps({"status": "enrolled", "repo": SENTINEL, "proof_digest": proof},
                     sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
