#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/policies"
printf 'policy\n' > "$TMP/policies/policy.md"
digest="$(shasum -a 256 "$TMP/policies/policy.md" | awk '{print $1}')"
run_validate() {
  PR_SAFETY_SNAPSHOT_ROOT="$TMP/snapshots" PR_SAFETY_POLICY_ROOT="$TMP/policies" \
  PR_SAFETY_POLICY_PATH="$TMP/policies/policy.md" PR_SAFETY_POLICY_VERSION=v1 \
  PR_SAFETY_POLICY_DIGEST="$1" PR_SAFETY_WORK_ROOT="$TMP/work" HANDOFF_ROOT="$TMP/handoffs" \
  scripts/validate-pr-safety-runtime.sh
}
run_validate "$digest"
[[ "$(stat -f '%Lp' "$TMP/work")" == 700 ]]
if run_validate "$(printf 0%.0s {1..64})" >/dev/null 2>&1; then
  echo "invalid policy digest accepted" >&2
  exit 1
fi
mkdir "$TMP/bin"
cat > "$TMP/bin/docker" <<'SH'
#!/bin/sh
printf '%s\n' "$*" > "$PR_SAFETY_TEST_DOCKER_ARGS"
SH
chmod +x "$TMP/bin/docker"
PR_SAFETY_TEST_DOCKER_ARGS="$TMP/docker-args" PATH="$TMP/bin:$PATH" \
PR_SAFETY_SNAPSHOT_ROOT="$TMP/snapshots" PR_SAFETY_POLICY_ROOT="$TMP/policies" \
PR_SAFETY_POLICY_PATH="$TMP/policies/policy.md" PR_SAFETY_POLICY_VERSION=v1 \
PR_SAFETY_POLICY_DIGEST="$digest" PR_SAFETY_WORK_ROOT="$TMP/work" HANDOFF_ROOT="$TMP/handoffs" \
scripts/pr-safety-up.sh
grep -q -- '--profile pr-safety up -d --build --wait pr-safety-review-controller' "$TMP/docker-args"
echo "PASS: PR safety runtime validation"
