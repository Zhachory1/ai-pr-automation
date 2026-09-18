#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import stat
from datetime import datetime, timedelta, timezone

HEX = {"credential_fingerprint", "ruleset_digest", "workflow_digest", "environment_policy_digest"}
DENIALS = {"api_merge", "protected_push", "unsafe_workflow_execution", "deployment", "administration"}
ALLOWED = {"unprotected_push", "draft_pr", "review"}


def fail(message):
    raise SystemExit(f"Hermes repository gate: {message}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence")
    args = parser.parse_args()
    descriptor = os.open(args.evidence, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            fail("evidence must be a regular file")
        try:
            evidence = json.load(source)
        except json.JSONDecodeError:
            fail("evidence must be JSON")
    expected = {"schema_version", "repo", "checked_at", *HEX, "denials", "allowed"}
    if not isinstance(evidence, dict) or set(evidence) != expected or evidence["schema_version"] != 1:
        fail("evidence shape changed")
    repo = evidence["repo"]
    if (not isinstance(repo, str) or repo.count("/") != 1
            or any(not part or not all(c.isalnum() or c in "._-" for c in part) for part in repo.split("/"))):
        fail("invalid repository")
    if any(not isinstance(evidence[key], str) or len(evidence[key]) != 64
           or any(c not in "0123456789abcdef" for c in evidence[key]) for key in HEX):
        fail("invalid authority digest")
    if (not isinstance(evidence["denials"], dict) or set(evidence["denials"]) != DENIALS
            or any(value is not True for value in evidence["denials"].values())):
        fail("all denial probes must pass")
    if (not isinstance(evidence["allowed"], dict) or set(evidence["allowed"]) != ALLOWED
            or any(value is not True for value in evidence["allowed"].values())):
        fail("all allowed probes must pass")
    try:
        checked = datetime.fromisoformat(evidence["checked_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        fail("invalid checked_at")
    now = datetime.now(timezone.utc)
    if checked.tzinfo is None or checked > now + timedelta(seconds=30) or now - checked > timedelta(minutes=10):
        fail("evidence is stale")
    authority = {key: value for key, value in evidence.items() if key != "checked_at"}
    canonical = json.dumps(authority, sort_keys=True, separators=(",", ":")).encode()
    result = {**evidence, "proof_digest": hashlib.sha256(canonical).hexdigest()}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
