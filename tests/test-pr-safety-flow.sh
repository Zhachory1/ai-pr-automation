#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
CID="pr-safety-flow-test-$$"
PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
TMP="$(mktemp -d)"
cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; chmod -R u+w "$TMP" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT
docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p "$PORT:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
for f in docker/initdb/{01-schema,02-agent-server,03-human-review-queue,04-pr-safety-review,05-pr-safety-merged-pr-producer}.sql; do docker cp "$f" "$CID:/tmp/${f##*/}"; docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f "/tmp/${f##*/}" >/dev/null; done

SOURCE="$TMP/source"; mkdir -p "$SOURCE"
git -C "$SOURCE" init -q; git -C "$SOURCE" config user.email test@example.com; git -C "$SOURCE" config user.name test
printf 'base\n' > "$SOURCE/x"; git -C "$SOURCE" add x; git -C "$SOURCE" commit -qm base
BASE="$(git -C "$SOURCE" rev-parse HEAD)"; printf 'head\n' >> "$SOURCE/x"; git -C "$SOURCE" commit -am head -q
HEAD="$(git -C "$SOURCE" rev-parse HEAD)"
DIFF="$(git -C "$SOURCE" diff --no-ext-diff "$BASE" "$HEAD" | shasum -a 256 | awk '{print $1}')"
POLICY_ROOT="$TMP/policies"; mkdir -p "$POLICY_ROOT"; printf 'changes_requested\n' > "$POLICY_ROOT/policy.md"
POLICY_DIGEST="$(shasum -a 256 "$POLICY_ROOT/policy.md" | awk '{print $1}')"

export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" PGPASSWORD=t OPENAI_API_KEY=test-key
export PR_SAFETY_MERGED_PR_AUTHORS=roktfleet
export PR_SAFETY_SNAPSHOT_ROOT="$TMP/snapshots" PR_SAFETY_POLICY_ROOT="$POLICY_ROOT" PR_SAFETY_POLICY_PATH="$POLICY_ROOT/policy.md" PR_SAFETY_POLICY_VERSION=v1 PR_SAFETY_POLICY_DIGEST="$POLICY_DIGEST"
export PR_SAFETY_WORK_ROOT="$TMP/work" HANDOFF_ROOT="$TMP/handoffs" PR_SAFETY_AGENT_DIR="$TMP/agent-config" PR_SAFETY_MEWRITE_BIN="$PWD/tests/fake-pr-safety-analyst.sh" PR_SAFETY_SERVER_ONCE=true
export PR_SAFETY_ANALYST_PROXY=http://pr-safety-egress:3128
export PR_SAFETY_TEST_REPO="$SOURCE"
mkdir -p "$TMP/bin" "$PR_SAFETY_WORK_ROOT" "$HANDOFF_ROOT" "$PR_SAFETY_AGENT_DIR/sessions"
# fake gh: only needs to satisfy the producer's snapshot clone from the local SOURCE repo.
cat > "$TMP/bin/gh" <<SH
#!/usr/bin/env bash
set -euo pipefail
case "\$1 \$2" in
  "repo clone") git clone --no-checkout "$SOURCE" "\$4" >/dev/null 2>&1 ;;
  *) exit 2 ;;
esac
SH
chmod +x "$TMP/bin/gh"
export PATH="$TMP/bin:$PATH"
chmod +x bin/pr-safety-merged-pr-producer bin/agent-server-pr-safety tests/fake-pr-safety-analyst.sh
q() { docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }

# one merged-PR record pointing at the local SOURCE repo's base/head as merge/base
printf '{"repo":"owner/repo","number":7,"mergeSha":"%s","baseSha":"%s"}\n' "$HEAD" "$BASE" > "$TMP/merged.jsonl"
PR_SAFETY_MERGED_PR_INPUT_FILE="$TMP/merged.jsonl" bin/pr-safety-merged-pr-producer
request_id="$(q "SELECT id FROM requests WHERE kind='pr-safety-review';")"
op="$(q "SELECT payload->>'operation_id' FROM requests WHERE id=$request_id;")"
slug="$(q "SELECT replace(payload->>'repo','/','__')||'__pr'||(payload->>'pr')||'__'||(payload->>'operation_id') FROM requests WHERE id=$request_id;")"
snapshot="$(q "SELECT payload->>'snapshot_path' FROM requests WHERE id=$request_id;")"
[[ -n "$op" && -z "$(git -C "$snapshot" status --porcelain --untracked-files=all)" && ! -w "$snapshot/x" ]]
[[ "$(q "SELECT (payload->>'base_sha')||'/'||(payload->>'head_sha')||'/'||(payload->>'diff_hash') FROM requests WHERE id=$request_id;")" == "$BASE/$HEAD/$DIFF" ]]
bin/agent-server-pr-safety
handoff="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$HANDOFF_ROOT/$slug.md")"
[[ "$(q "SELECT status FROM requests WHERE id=$request_id;")" == "done" ]]
[[ -f "$handoff" && "$(stat -f '%Lp' "$handoff")" == 600 ]]
[[ "$(q "SELECT count(*) FROM pending_maintenance_reviews WHERE request_id=$request_id AND provenance->>'operation_id'='$op';")" == 1 ]]
[[ "$(q "SELECT (provenance->>'handoff_path')||'/'||(provenance->>'handoff_digest') FROM pending_maintenance_reviews WHERE request_id=$request_id;")" == "$handoff/$(shasum -a 256 "$handoff" | awk '{print $1}')" ]]
[[ "$(q "SELECT (provenance->>'base_sha')||'/'||(provenance->>'head_sha')||'/'||(provenance->>'diff_hash')||'/'||(provenance->>'policy_digest') FROM pending_maintenance_reviews WHERE request_id=$request_id;")" == "$BASE/$HEAD/$DIFF/$POLICY_DIGEST" ]]
grep -Fq "$HEAD" "$handoff"
# single-container contract: direct agent process, trusted cwd, no nested Docker or analyst image
! grep -Fq '/var/run/docker.sock' docker-compose.yml
! grep -Fq 'docker run' bin/agent-server-pr-safety
grep -A25 '^  agent-server-pr-safety:' docker-compose.yml | grep -Fq 'pids_limit: 256'
grep -A25 '^  agent-server-pr-safety:' docker-compose.yml | grep -Fq 'mem_limit: 4g'
grep -A25 '^  agent-server-pr-safety:' docker-compose.yml | grep -Fq 'cpus: 2'
grep -Fq 'exec --skip-git-repo-check --model' bin/agent-server-pr-safety
grep -Fq -- '--cwd "$work"' bin/agent-server-pr-safety
[[ ! -e bin/pr-safety-review-controller && ! -e bin/pr-safety-review-runner ]]
[[ ! -e Dockerfile.pr-safety-controller && ! -e Dockerfile.pr-safety-analyst && ! -e Dockerfile.agent-server-pr-safety ]]
grep -A10 '^  agent-server-pr-safety:' docker-compose.yml | grep -Fq 'dockerfile: Dockerfile.agent-server'
jq -e '.mcpServers["hindsight-world"].command == "mcp-remote" and .mcpServers["hindsight-pr-safety"].command == "mcp-remote"' agent-config/pr-safety-mcp.json >/dev/null
echo "PASS: merged PR reaches immutable handoff and human queue"
