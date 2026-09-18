#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    gate = subprocess.run([str(ROOT / "scripts/hermes-repo-gate.py"), str(args.evidence)],
                          capture_output=True, text=True, timeout=30)
    if gate.returncode:
        raise SystemExit(gate.stderr.strip() or "authority probe failed")
    value = json.loads(gate.stdout)
    command = ["psql", "-qAt", "-v", "ON_ERROR_STOP=1", "-h", os.environ.get("REQUESTS_DB_HOST", "localhost"),
               "-p", os.environ.get("REQUESTS_DB_PORT", "5432"), "-U", os.environ.get("REQUESTS_DB_USER", "fleet"),
               "-d", os.environ.get("REQUESTS_DB_NAME", "fleet"), "-v", f"repo={value['repo']}",
               "-v", f"proof={value['proof_digest']}", "-v", f"checked={value['checked_at']}"]
    sql = """
UPDATE hermes_repository_enrollments
   SET checked_at=:'checked'::timestamptz
 WHERE repo=:'repo' AND active AND proof_digest=:'proof'
RETURNING 1;
"""
    result = subprocess.run(command, input=sql, capture_output=True, text=True, timeout=30)
    if result.returncode or result.stdout.strip() != "1":
        invalidate = subprocess.run(command, input="""
UPDATE hermes_repository_enrollments SET active=false,invalid_reason='authority proof changed'
 WHERE repo=:'repo' AND active RETURNING 1;
""", capture_output=True, text=True, timeout=30)
        if invalidate.returncode:
            raise SystemExit("authority invalidation failed")
        raise SystemExit("authority proof changed; repository invalidated")
    print(json.dumps({"status": "current", "repo": value["repo"], "proof_digest": value["proof_digest"]},
                     sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
