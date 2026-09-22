#!/usr/bin/env bash
# One operator command: Compose owns queue control; launchd keeps only Hermes gateway/dashboard.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SHARED_RUNTIME="${HERMES_SHARED_RUNTIME_ROOT:-/Users/Shared/ai-pr-automation-runtime}"
# Exported values override stale/missing .env values and match the service-account role bootstrap.
export DOC_WRITER_STAGE_HOST="${DOC_WRITER_STAGE_HOST:-$SHARED_RUNTIME/doc-writer}"
export HANDOFF_ROOT="${HANDOFF_ROOT:-$SHARED_RUNTIME/safety-handoffs}"
export HERMES_API_KEYS_FILE="${HERMES_API_KEYS_FILE:-/Users/Shared/ai-pr-automation-runtime/secrets/hermes-api-keys.json}"
export GITHUB_READ_TOKEN_FILE="${GITHUB_READ_TOKEN_FILE:-/Users/Shared/ai-pr-automation-runtime/secrets/github-read-token}"
AUTHORITY_SOURCE="${HERMES_AUTHORITY_SOURCE_FILE:-${HERMES_AUTHORITY_FILE:-/usr/local/etc/ai-pr-automation/authority.yaml}}"
DOCKER_AUTHORITY="${HERMES_DOCKER_AUTHORITY_FILE:-/Users/Shared/zhach-ai-pr-automation/authority.yaml}"
# Override stale pre-native values from .env with the shared runtime paths used by Compose.
export PR_SAFETY_MERGED_PR_AUTHORS="${PR_SAFETY_MERGED_PR_AUTHORS:-roktfleet,brucerokt}"
export PR_SAFETY_ALLOWED_ORGS="${PR_SAFETY_ALLOWED_ORGS:-ROKT}"
export PR_SAFETY_SNAPSHOT_ROOT="${PR_SAFETY_SNAPSHOT_ROOT:-$SHARED_RUNTIME/safety-snapshots}"
export PR_SAFETY_POLICY_ROOT="${PR_SAFETY_POLICY_ROOT:-/usr/local/etc/ai-pr-automation}"
export PR_SAFETY_POLICY_PATH="${PR_SAFETY_POLICY_PATH:-$PR_SAFETY_POLICY_ROOT/pr-safety-policy-v1.md}"
export PR_SAFETY_POLICY_VERSION="${PR_SAFETY_POLICY_VERSION:-v1}"
export PR_SAFETY_POLICY_DIGEST="${PR_SAFETY_POLICY_DIGEST:-$(shasum -a 256 "$ROOT/policy/pr-safety-policy-v1.md" | awk '{print $1}')}"

case "${1:-}" in
  up)
    "$ROOT/scripts/hermes-authority.py" --file "$AUTHORITY_SOURCE" >/dev/null
    install -d -m 0700 "$(dirname "$DOCKER_AUTHORITY")"
    [[ ! -L "$DOCKER_AUTHORITY" && ( ! -e "$DOCKER_AUTHORITY" || -f "$DOCKER_AUTHORITY" ) ]] \
      || { echo "invalid Docker authority mirror: $DOCKER_AUTHORITY" >&2; exit 2; }
    cat "$AUTHORITY_SOURCE" > "$DOCKER_AUTHORITY"
    chmod 0644 "$DOCKER_AUTHORITY"
    export HERMES_AUTHORITY_FILE="$DOCKER_AUTHORITY"
    sudo "$ROOT/scripts/configure-hermes-role-env.sh"
    # Provision profile API keys/listener before Compose resolves its controller-only secret.
    sudo "$ROOT/scripts/hermes-native.sh" sync-support
    sudo "$ROOT/scripts/hermes-native.sh" start
    sudo "$ROOT/scripts/hermes-native.sh" dashboard-start
    "$ROOT/scripts/compose.sh" --profile hermes-api-conformance run --rm hermes-api-conformance
    "$ROOT/scripts/compose.sh" up -d --build
    ;;
  down)
    "$ROOT/scripts/compose.sh" down
    sudo "$ROOT/scripts/hermes-native.sh" down
    ;;
  status)
    echo '=== Compose support services ==='
    "$ROOT/scripts/compose.sh" ps
    echo '=== Host-native Hermes ==='
    "$ROOT/scripts/hermes-native.sh" status
    ;;
  logs)
    echo '=== Compose controller/producers ==='
    "$ROOT/scripts/compose.sh" logs --tail=200 hermes-controller pr-producer-review pr-producer-maintain pr-safety-producer memory-curate-producer
    echo '=== Host-native Hermes ==='
    sudo "$ROOT/scripts/hermes-native.sh" logs
    ;;
  *) echo "usage: $0 up|down|status|logs" >&2; exit 2 ;;
esac
