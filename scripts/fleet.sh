#!/usr/bin/env bash
# One operator command: Compose owns queue control; launchd keeps Hermes gateway/dashboard/bridge.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SHARED_RUNTIME="${HERMES_SHARED_RUNTIME_ROOT:-/Users/Shared/ai-pr-automation-runtime}"
# Exported values override stale/missing .env values and match the service-account role bootstrap.
export DOC_WRITER_STAGE_HOST="${DOC_WRITER_STAGE_HOST:-$SHARED_RUNTIME/doc-writer}"
export HANDOFF_ROOT="${HANDOFF_ROOT:-$SHARED_RUNTIME/safety-handoffs}"
export HERMES_API_KEYS_FILE="${HERMES_API_KEYS_FILE:-/Users/Shared/ai-pr-automation-runtime/secrets/hermes-api-keys.json}"
export HERMES_KANBAN_BRIDGE_KEY_FILE="${HERMES_KANBAN_BRIDGE_KEY_FILE:-$SHARED_RUNTIME/hermes-bridge-secrets/key.json}"
export HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE="${HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE:-$SHARED_RUNTIME/secrets/hermes-kanban-bridge-key.json}"
export PR_SAFETY_QUEUE_ENGINE="${PR_SAFETY_QUEUE_ENGINE:-postgres}"
[[ "$PR_SAFETY_QUEUE_ENGINE" == postgres || "$PR_SAFETY_QUEUE_ENGINE" == kanban ]] \
  || { echo "PR_SAFETY_QUEUE_ENGINE must be postgres or kanban" >&2; exit 2; }
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

recreate_producer() {
  local mode="$1" service="$2" variable="$3" engine="$4" container state environment matches
  env "$variable=$engine" "$ROOT/scripts/compose.sh" up -d --no-deps --force-recreate "$service" || return
  container="$("$ROOT/scripts/compose.sh" ps -q "$service")" || return
  [[ -n "$container" && "$container" != *$'\n'* ]] \
    || { echo "expected one $service container" >&2; return 1; }
  state="$(docker inspect --format '{{.State.Running}}' "$container")" || return
  environment="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container")" || return
  matches="$(grep -Fxc "$variable=$engine" <<<"$environment" || true)"
  [[ "$state" == true && "$matches" == 1 ]] \
    || { echo "$mode producer failed $engine environment/running verification" >&2; return 1; }
}

case "${1:-}" in
  up)
    "$ROOT/scripts/hermes-authority.py" --file "$AUTHORITY_SOURCE" >/dev/null
    "$ROOT/scripts/compose.sh" config --format json | python3 -c '
import json, os, pathlib, stat, sys
secrets = json.load(sys.stdin)["secrets"]
for name in ("github_discovery_token", "hermes_review_key"):
    path = pathlib.Path(secrets[name]["file"])
    try:
        info, parent = path.lstat(), path.parent.lstat()
    except OSError:
        raise SystemExit(f"configure owner-only {name} before fleet up")
    owners = {0, os.getuid()}
    if (not stat.S_ISREG(info.st_mode) or info.st_size == 0 or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid not in owners or not stat.S_ISDIR(parent.st_mode)
            or stat.S_IMODE(parent.st_mode) & 0o077 or parent.st_uid not in owners):
        raise SystemExit(f"configure owner-only {name} before fleet up")
'
    sudo "$ROOT/scripts/hermes-native.sh" review-producer-stop
    sudo "$ROOT/scripts/hermes-native.sh" producer-stop
    "$ROOT/scripts/compose.sh" stop pr-safety-producer
    install -d -m 0700 "$(dirname "$DOCKER_AUTHORITY")"
    [[ ! -L "$DOCKER_AUTHORITY" && ( ! -e "$DOCKER_AUTHORITY" || -f "$DOCKER_AUTHORITY" ) ]] \
      || { echo "invalid Docker authority mirror: $DOCKER_AUTHORITY" >&2; exit 2; }
    cat "$AUTHORITY_SOURCE" > "$DOCKER_AUTHORITY"
    chmod 0644 "$DOCKER_AUTHORITY"
    export HERMES_AUTHORITY_FILE="$DOCKER_AUTHORITY"
    sudo env PR_SAFETY_QUEUE_ENGINE="$PR_SAFETY_QUEUE_ENGINE" \
      HERMES_AUTHORITY_FILE="$AUTHORITY_SOURCE" \
      PR_SAFETY_MERGED_PR_AUTHORS="$PR_SAFETY_MERGED_PR_AUTHORS" \
      PR_SAFETY_ALLOWED_ORGS="$PR_SAFETY_ALLOWED_ORGS" "$ROOT/scripts/configure-hermes-role-env.sh"
    sudo env HERMES_KANBAN_BRIDGE_KEY_FILE="$HERMES_KANBAN_BRIDGE_KEY_FILE" "$ROOT/scripts/hermes-native.sh" up
    [[ -f "$HERMES_KANBAN_BRIDGE_KEY_FILE" ]] \
      || { echo "Kanban bridge key unavailable: $HERMES_KANBAN_BRIDGE_KEY_FILE" >&2; exit 2; }
    sudo install -m 0600 -o "$(id -u)" -g "$(id -g)" \
      "$HERMES_KANBAN_BRIDGE_KEY_FILE" "$HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE"
    [[ -r "$HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE" ]] \
      || { echo "Kanban controller key unavailable: $HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE" >&2; exit 2; }
    sudo env HERMES_KANBAN_BRIDGE_KEY_FILE="$HERMES_KANBAN_BRIDGE_KEY_FILE" "$ROOT/scripts/hermes-native.sh" bridge-start
    "$ROOT/scripts/compose.sh" --profile hermes-api-conformance run --rm hermes-api-conformance
    "$ROOT/scripts/compose.sh" up -d --build
    sudo "$ROOT/scripts/hermes-native.sh" producer-start
    ;;
  down)
    sudo "$ROOT/scripts/hermes-native.sh" producer-stop
    "$ROOT/scripts/compose.sh" down
    sudo "$ROOT/scripts/hermes-native.sh" down
    ;;
  maintain-kanban-up)
    recreate_producer maintain pr-producer-maintain PR_MAINTAIN_QUEUE_ENGINE kanban
    if ! sudo "$ROOT/scripts/hermes-native.sh" maintain-mode-set-kanban; then
      sudo "$ROOT/scripts/hermes-native.sh" maintain-producer-stop || true
      exit 1
    fi
    ;;
  maintain-postgres-up)
    sudo "$ROOT/scripts/hermes-native.sh" maintain-producer-stop
    recreate_producer maintain pr-producer-maintain PR_MAINTAIN_QUEUE_ENGINE postgres
    ;;
  memory-cron-up)
    if ! recreate_producer memory memory-curate-producer MEMORY_CURATE_QUEUE_ENGINE cron \
      || ! sudo "$ROOT/scripts/hermes-native.sh" memory-cron-start; then
      sudo "$ROOT/scripts/hermes-native.sh" memory-cron-stop || true
      exit 1
    fi
    ;;
  memory-postgres-up)
    sudo "$ROOT/scripts/hermes-native.sh" memory-cron-stop
    recreate_producer memory memory-curate-producer MEMORY_CURATE_QUEUE_ENGINE postgres
    ;;
  repo-cache-install)
    [[ "$#" == 1 ]] || { echo "usage: $0 repo-cache-install" >&2; exit 2; }
    sudo "$ROOT/scripts/hermes-native.sh" repo-cache-install
    ;;
  repo-cache-enroll)
    [[ "$#" == 2 || "$#" == 3 ]] || { echo "usage: $0 repo-cache-enroll OWNER/REPO [absolute-seed]" >&2; exit 2; }
    sudo "$ROOT/scripts/hermes-native.sh" repo-cache-enroll "${@:2}"
    ;;
  repo-cache-sync|repo-cache-materialize|repo-cache-status)
    [[ "$#" == 2 ]] || { echo "usage: $0 $1 OWNER/REPO" >&2; exit 2; }
    sudo "$ROOT/scripts/hermes-native.sh" "$1" "$2"
    ;;
  prd-canary-enqueue)
    [[ "$#" == 2 ]] || { echo "usage: $0 prd-canary-enqueue <absolute-json-file>" >&2; exit 2; }
    sudo env PRD_WORKFLOW_ENGINE="${PRD_WORKFLOW_ENGINE:-fixed}" \
      "$ROOT/scripts/hermes-native.sh" prd-canary-enqueue "$2"
    ;;
  design-canary-enqueue|roadmap-canary-enqueue)
    [[ "$#" == 2 ]] || { echo "usage: $0 $1 <absolute-json-file>" >&2; exit 2; }
    sudo "$ROOT/scripts/hermes-native.sh" "$1" "$2"
    ;;
  prd-canary-advance)
    [[ "$#" == 2 ]] || { echo "usage: $0 prd-canary-advance <prd-operation-id>" >&2; exit 2; }
    sudo "$ROOT/scripts/hermes-native.sh" prd-canary-advance "$2"
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
  *) echo "usage: $0 up|down|maintain-kanban-up|maintain-postgres-up|memory-cron-up|memory-postgres-up|repo-cache-install|repo-cache-enroll|repo-cache-sync|repo-cache-materialize|repo-cache-status|prd-canary-enqueue|design-canary-enqueue|roadmap-canary-enqueue|prd-canary-advance|status|logs" >&2; exit 2 ;;
esac
