#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a; . "$ROOT/.env"; set +a
fi

: "${PR_SAFETY_SNAPSHOT_ROOT:?set PR_SAFETY_SNAPSHOT_ROOT}"
: "${PR_SAFETY_POLICY_ROOT:?set PR_SAFETY_POLICY_ROOT}"
: "${PR_SAFETY_POLICY_PATH:?set PR_SAFETY_POLICY_PATH}"
: "${PR_SAFETY_POLICY_VERSION:?set PR_SAFETY_POLICY_VERSION}"
: "${PR_SAFETY_POLICY_DIGEST:?set PR_SAFETY_POLICY_DIGEST}"
: "${PR_SAFETY_WORK_ROOT:?set PR_SAFETY_WORK_ROOT}"
: "${HANDOFF_ROOT:?set HANDOFF_ROOT}"

python3 - "$PR_SAFETY_SNAPSHOT_ROOT" "$PR_SAFETY_POLICY_ROOT" "$PR_SAFETY_POLICY_PATH" "$PR_SAFETY_WORK_ROOT" "$HANDOFF_ROOT" "$PR_SAFETY_POLICY_DIGEST" <<'PY'
import hashlib
import os
import sys

snapshot, policy_root, policy, work, handoff, expected_digest = sys.argv[1:]
paths = [snapshot, policy_root, policy, work, handoff]
if not all(os.path.isabs(path) for path in paths):
    raise SystemExit("PR safety paths must be absolute")
policy_root = os.path.realpath(policy_root)
policy = os.path.realpath(policy)
if os.path.commonpath((policy_root, policy)) != policy_root or not os.path.isfile(policy):
    raise SystemExit("PR_SAFETY_POLICY_PATH must be a regular file beneath PR_SAFETY_POLICY_ROOT")
actual_digest = hashlib.file_digest(open(policy, "rb"), "sha256").hexdigest()
if actual_digest != expected_digest:
    raise SystemExit("PR_SAFETY_POLICY_DIGEST does not match PR_SAFETY_POLICY_PATH")
PY

mkdir -p "$PR_SAFETY_SNAPSHOT_ROOT" "$PR_SAFETY_WORK_ROOT" "$HANDOFF_ROOT"
chmod 700 "$PR_SAFETY_SNAPSHOT_ROOT" "$PR_SAFETY_WORK_ROOT" "$HANDOFF_ROOT"
