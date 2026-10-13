#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
cleanup() { chmod -R u+w "$TMP" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

SOURCE="$TMP/source"; mkdir -p "$SOURCE"
git -C "$SOURCE" init -q; git -C "$SOURCE" config user.email t@e.com; git -C "$SOURCE" config user.name t
printf 'base\n' > "$SOURCE/x"; git -C "$SOURCE" add x; git -C "$SOURCE" commit -qm base
BASE="$(git -C "$SOURCE" rev-parse HEAD)"
printf 'head\n' >> "$SOURCE/x"; git -C "$SOURCE" commit -am head -q
MERGE="$(git -C "$SOURCE" rev-parse HEAD)"
printf 'head2\n' >> "$SOURCE/x"; git -C "$SOURCE" commit -am head2 -q
MERGE2="$(git -C "$SOURCE" rev-parse HEAD)"

POLICY_ROOT="$TMP/policies"; mkdir -p "$POLICY_ROOT"; printf 'pinned policy\n' > "$POLICY_ROOT/policy.md"
if env -u PR_SAFETY_QUEUE_ENGINE bin/hermes-pr-safety-producer >/dev/null 2>&1; then
  echo 'FAIL: optional PR-safety admission enabled without explicit Kanban selection' >&2; exit 1
fi
export PR_SAFETY_QUEUE_ENGINE=kanban PR_SAFETY_MERGED_PR_AUTHORS=roktfleet
export PR_SAFETY_SNAPSHOT_ROOT="$TMP/snapshots" PR_SAFETY_POLICY_ROOT="$POLICY_ROOT" PR_SAFETY_POLICY_PATH="$POLICY_ROOT/policy.md" PR_SAFETY_POLICY_VERSION=v1
export PR_SAFETY_POLICY_DIGEST="$(shasum -a 256 "$POLICY_ROOT/policy.md" | awk '{print $1}')"
mkdir -p "$TMP/bin"
cat > "$TMP/authority.yaml" <<'EOF'
repos:
  - owner/*
EOF
export HERMES_AUTHORITY_FILE="$TMP/authority.yaml" HERMES_AUTHORITY_BIN="$PWD/scripts/hermes-authority.py"
cat > "$TMP/bin/gh" <<SH
#!/usr/bin/env bash
set -euo pipefail
case "\$1 \$2" in
  "search prs") cat "\$PR_SAFETY_TEST_SEARCH_JSON" ;;
  "api repos"*) cat "\$PR_SAFETY_TEST_VIEW_JSON" ;;
  "repo clone") git clone --no-checkout "$SOURCE" "\$4" >/dev/null 2>&1 ;;
  *) exit 2 ;;
esac
SH
cat > "$TMP/bin/psql" <<'SH'
#!/usr/bin/env bash
echo 'FAIL: request database accessed' >&2
exit 99
SH
cat > "$TMP/bin/hermes" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat > "$TMP/fake-kanban-enqueue.py" <<'PY'
import json, os, pathlib, sys
payload=json.loads(sys.stdin.read())
with pathlib.Path(os.environ["PR_SAFETY_TEST_KANBAN_CAPTURE"]).open("a") as output:
    output.write(json.dumps(payload, sort_keys=True) + "\n")
if payload["pr"] == 8: raise SystemExit("fixture enqueue failure")
print(json.dumps({"status":"enqueued","board":"pr-safety-test","task_ids":{"review":"t1"}}))
PY
chmod +x "$TMP/bin/gh" "$TMP/bin/psql" "$TMP/bin/hermes"
export PATH="$TMP/bin:$PATH" PR_SAFETY_TEST_KANBAN_CAPTURE="$TMP/kanban-payload.jsonl"
export HERMES_PR_SAFETY_KANBAN_ENQUEUE="$TMP/fake-kanban-enqueue.py" HERMES_BIN="$TMP/bin/hermes"
export HERMES_PYTHON="$(command -v python3)"
touch "$TMP/contract.json" "$TMP/risk.py"
export HERMES_KANBAN_BRIDGE_CONTRACT="$TMP/contract.json" HERMES_KANBAN_RISK_COUNCIL="$TMP/risk.py"
fail=0; check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

printf '[{"number":7,"repository":{"nameWithOwner":"owner/repo"}}]\n' > "$TMP/search.json"
printf '{"number":7,"state":"closed","merge_commit_sha":"%s","base":{"sha":"%s"},"merged_at":"2026-01-01T00:00:00Z"}\n' "$MERGE" "$BASE" > "$TMP/view.json"
export PR_SAFETY_TEST_SEARCH_JSON="$TMP/search.json" PR_SAFETY_TEST_VIEW_JSON="$TMP/view.json"
bin/hermes-pr-safety-producer
check "merged PR admits immutable snapshot" "jq -e --arg head '$MERGE' --arg base '$BASE' --arg digest '$PR_SAFETY_POLICY_DIGEST' '(.head_sha==\$head) and (.base_sha==\$base) and (.policy_digest==\$digest) and (.snapshot_path|contains(\"/direct-kanban/\"))' '$TMP/kanban-payload.jsonl' >/dev/null"
snapshot="$(jq -r .snapshot_path "$TMP/kanban-payload.jsonl")"
check "snapshot is clean and read-only" "[[ -z \"\$(git -C '$snapshot' status --porcelain --untracked-files=all)\" && ! -w '$snapshot/x' ]]"

printf '{"repo":"owner/repo","number":8,"mergeSha":"%s","baseSha":"%s"}\n' "$MERGE2" "$BASE" > "$TMP/records.jsonl"
printf '{"repo":"owner/repo","number":9,"mergeSha":"%s","baseSha":"%s"}\n' "$MERGE2" "$BASE" >> "$TMP/records.jsonl"
if PR_SAFETY_MERGED_PR_INPUT_FILE="$TMP/records.jsonl" bin/hermes-pr-safety-producer; then batchrc=0; else batchrc=$?; fi
check "failed first admission does not starve later PRs" "[[ '$batchrc' == 1 ]] && jq -s -e 'length==3 and .[2].pr==9' '$TMP/kanban-payload.jsonl' >/dev/null"

printf '{"number":8,"state":"open","merge_commit_sha":null,"base":{"sha":"%s"},"merged_at":null}\n' "$BASE" > "$TMP/view.json"
printf '[{"number":8,"repository":{"nameWithOwner":"owner/repo"}}]\n' > "$TMP/search.json"
bin/hermes-pr-safety-producer
check "non-merged PR is ignored" "[[ \$(wc -l < '$TMP/kanban-payload.jsonl') -eq 3 ]]"
printf '{"number":11,"state":"closed","merge_commit_sha":"%s","base":{"sha":"%s"},"merged_at":"2026-01-03T00:00:00Z"}\n' "$MERGE2" "$BASE" > "$TMP/view.json"
printf '[{"number":11,"repository":{"nameWithOwner":"other/repo"}}]\n' > "$TMP/search.json"
bin/hermes-pr-safety-producer
check "ungranted repo is ignored" "[[ \$(wc -l < '$TMP/kanban-payload.jsonl') -eq 3 ]]"

cat > "$TMP/bin/gh" <<'SH'
#!/usr/bin/env bash
echo "GraphQL: Resource protected by organization SAML enforcement." >&2
exit 1
SH
chmod +x "$TMP/bin/gh"
if bin/hermes-pr-safety-producer >/dev/null 2>"$TMP/saml.err"; then samlrc=0; else samlrc=$?; fi
check "expired SAML is loud fatal" "[[ '$samlrc' == 4 ]] && grep -q 'SAML authorization expired' '$TMP/saml.err'"
(( fail == 0 )) && echo 'PASS: Kanban PR-safety discovery and snapshot admission' || exit 1
