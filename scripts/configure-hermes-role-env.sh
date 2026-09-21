#!/usr/bin/env bash
# One-time non-secret role defaults for the dedicated service account. Preserves provider/GitHub
# credentials, removes retired queue DB credentials, and replaces named role-path settings below.
set -euo pipefail
[[ "$EUID" == 0 ]] || { echo 'run as root' >&2; exit 2; }
SERVICE_USER="${HERMES_SERVICE_USER:-hermes-agent}"
SERVICE_HOME="${HERMES_SERVICE_HOME:-/Users/$SERVICE_USER}"
HERMES_HOME="${HERMES_NATIVE_HOME:-$SERVICE_HOME/.hermes}"
CONFIG_ROOT="${HERMES_NATIVE_CONFIG_ROOT:-/usr/local/etc/ai-pr-automation}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$HERMES_HOME/.env"
# Docker Desktop cannot traverse the intentionally 0700 service home. Cross-runtime artifacts live
# in a dedicated shared root: hermes-agent owns writes; staff can traverse/read Fleet Controller's
# read-only bind mounts. Provider credentials remain under the private service home.
STATE="${HERMES_SHARED_RUNTIME_ROOT:-/Users/Shared/ai-pr-automation-runtime}"
DOC_STAGE="$STATE/doc-writer"
MEMORY_STATE="$STATE/memory-curator"
HANDOFF="$STATE/safety-handoffs"
SNAPSHOTS="$STATE/safety-snapshots"
POLICY="$CONFIG_ROOT/pr-safety-policy-v1.md"

install -d -m 0755 -o root -g wheel "$STATE"
install -d -m 0770 -o "$SERVICE_USER" -g staff "$DOC_STAGE" "$HANDOFF" "$MEMORY_STATE" "$SNAPSHOTS"
install -m 0444 -o root -g wheel "$ROOT/policy/pr-safety-policy-v1.md" "$POLICY"
POLICY_DIGEST="$(shasum -a 256 "$POLICY" | awk '{print $1}')"

tmp="$(mktemp /private/tmp/hermes-role-env.XXXXXX)"; trap 'rm -f "$tmp"' EXIT
if [[ -f "$ENV_FILE" ]]; then
  grep -vE '^(PGPASSWORD|REQUESTS_DB_USER|REQUESTS_DB_NAME|REQUESTS_DB_HOST|REQUESTS_DB_PORT|DOC_WRITER_STAGE_DIR|DOC_WRITER_INBOX_DIR|MEMORY_CURATOR_PRIVATE_DOCS|MEMORY_CURATOR_STATE_DIR|PR_SAFETY_MERGED_PR_AUTHORS|PR_SAFETY_SNAPSHOT_ROOT|PR_SAFETY_POLICY_ROOT|PR_SAFETY_POLICY_PATH|PR_SAFETY_POLICY_VERSION|PR_SAFETY_POLICY_DIGEST|HANDOFF_ROOT)=' "$ENV_FILE" > "$tmp" || true
fi
cat >> "$tmp" <<EOF
DOC_WRITER_STAGE_DIR=$DOC_STAGE
DOC_WRITER_INBOX_DIR=${HERMES_DOC_WRITER_INBOX_DIR:-/Users/zhach/private-docs/inbox}
MEMORY_CURATOR_PRIVATE_DOCS=${HERMES_MEMORY_PRIVATE_DOCS:-/Users/zhach/private-docs}
MEMORY_CURATOR_STATE_DIR=$MEMORY_STATE
PR_SAFETY_MERGED_PR_AUTHORS=${HERMES_PR_SAFETY_AUTHORS:-Zhachory1,zhach1}
PR_SAFETY_SNAPSHOT_ROOT=$SNAPSHOTS
PR_SAFETY_POLICY_ROOT=$CONFIG_ROOT
PR_SAFETY_POLICY_PATH=$POLICY
PR_SAFETY_POLICY_VERSION=v1
PR_SAFETY_POLICY_DIGEST=$POLICY_DIGEST
HANDOFF_ROOT=$HANDOFF
EOF
install -m 0600 -o "$SERVICE_USER" -g staff "$tmp" "$ENV_FILE"
echo "configured Hermes role paths: stage=$DOC_STAGE memory=$MEMORY_STATE handoff=$HANDOFF snapshots=$SNAPSHOTS"
