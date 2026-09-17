#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/shim" "$tmp/work/worktree"

cat > "$tmp/bin/hermes" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "$TEST_STATE/hermes.args"
printf '%s\n%s\n' "$HERMES_HOME" "$HERMES_WRITE_SAFE_ROOT" > "$TEST_STATE/hermes.env"
query_file=""
while (($#)); do
  [[ "$1" != --query-file ]] || { query_file="$2"; shift; }
  shift
done
cmp "$TEST_STATE/prompt.expected" "$query_file"
git push origin "HEAD:$PR_HEAD_BRANCH"
case "${FAKE_HERMES_MODE:-valid}" in
  valid)
    jq -cn --arg nonce "$AGENT_RUN_NONCE" '{nonce:$nonce,verdict:"comment",findings:[],summary:"fake Hermes",maintenance:{needs_human_review:false}}' > "$AGENT_RESULT_FILE"
    ;;
  invalid)
    jq -cn --arg nonce "$AGENT_RUN_NONCE" '{nonce:$nonce,verdict:"comment",findings:[],summary:"bad",maintenance:{needs_human_review:false},extra:true}' > "$AGENT_RESULT_FILE"
    ;;
  fail) exit 7 ;;
esac
printf 'fake Hermes output\n'
SH
cat > "$tmp/shim/git" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$TEST_STATE/git.args"
SH
chmod +x "$tmp/bin/hermes" "$tmp/shim/git"

# shellcheck disable=SC2016
printf '%s\n' 'literal prompt $(touch should-not-run)' > "$tmp/prompt.md"
cp "$tmp/prompt.md" "$tmp/prompt.expected"
common_env=(
  "HERMES_BIN=$tmp/bin/hermes"
  "TEST_STATE=$tmp"
  "PR_WORK_ROOT=$tmp/work"
  "PR_HEAD_BRANCH=feature/exact"
  "AGENT_RUN_NONCE=nonce-123"
  "PATH=$tmp/shim:$PATH"
)
env "${common_env[@]}" AGENT_RESULT_FILE="$tmp/result.json" \
  bin/hermes-maintain-runner.sh "$tmp/prompt.md"

cat > "$tmp/expected.args" <<EOF
chat
--query-file
$tmp/prompt.md
--oneshot
--model
gpt-5.6-sol
--provider
openai-api
--toolsets
terminal,file
--max-turns
100
--run-budget
1500
--safe-mode
--ignore-rules
--yolo
--in
$tmp/work/worktree
EOF
diff -u "$tmp/expected.args" "$tmp/hermes.args"
printf '%s\n%s\n' "$tmp/work/hermes" "$tmp/work" > "$tmp/expected.env"
diff -u "$tmp/expected.env" "$tmp/hermes.env"
printf '%s\n' push origin HEAD:feature/exact > "$tmp/expected.git.args"
diff -u "$tmp/expected.git.args" "$tmp/git.args"
jq -e 'keys == ["findings","maintenance","nonce","summary","verdict"] and .nonce == "nonce-123" and .maintenance.needs_human_review == false' "$tmp/result.json" >/dev/null
[[ ! -e should-not-run ]]

rm -f "$tmp/result.json"
set +e
env "${common_env[@]}" FAKE_HERMES_MODE=missing AGENT_RESULT_FILE="$tmp/result.json" \
  bin/hermes-maintain-runner.sh "$tmp/prompt.md"
status=$?
set -e
[[ "$status" == 2 && ! -e "$tmp/result.json" ]]

set +e
env "${common_env[@]}" FAKE_HERMES_MODE=invalid AGENT_RESULT_FILE="$tmp/result.json" \
  bin/hermes-maintain-runner.sh "$tmp/prompt.md"
status=$?
set -e
[[ "$status" == 2 ]]
jq -e '.extra == true and .summary == "bad"' "$tmp/result.json" >/dev/null

rm -f "$tmp/result.json"
set +e
env "${common_env[@]}" FAKE_HERMES_MODE=fail AGENT_RESULT_FILE="$tmp/result.json" \
  bin/hermes-maintain-runner.sh "$tmp/prompt.md"
status=$?
set -e
[[ "$status" == 7 && ! -e "$tmp/result.json" ]]

awk '/^validate_maintain_result\(\) \{/{p=1} p{print} p&&/^\}/{exit}' bin/agent-server > "$tmp/controller-functions.sh"
awk '/^process_one\(\) \{/{p=1} p{print} p&&/^\}/{exit}' bin/agent-server >> "$tmp/controller-functions.sh"
# shellcheck disable=SC1090
. "$tmp/controller-functions.sh"

cat > "$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$TEST_PR_INFO"
SH
cat > "$tmp/bin/timeout" <<'SH'
#!/usr/bin/env bash
[[ "${1:-}" != --kill-after=* ]] || shift
[[ "${1:-}" != *s ]] || shift
exec "$@"
SH
cat > "$tmp/bin/controller-runner" <<'SH'
#!/usr/bin/env bash
[[ "$PR_HEAD_BRANCH" == feature/exact ]]
printf '{"nonce":"%s","verdict":"comment"}\n' "$AGENT_RUN_NONCE" > "$AGENT_RESULT_FILE"
SH
chmod +x "$tmp/bin/gh" "$tmp/bin/timeout" "$tmp/bin/controller-runner"

queue_mark_failed() { printf '%s\n' "$2" > "$tmp/controller.failed"; }
queue_mark_superseded() { printf '%s\n' "$2" > "$tmp/controller.superseded"; }
queue_mark_reconcile() { printf '%s\n' "$2" > "$tmp/controller.reconcile"; }
queue_mark_done() { touch "$tmp/controller.done"; printf '1\n'; }
queue_mark_side_effect() { printf '1\n'; }
apply_post_run_routing() { touch "$tmp/controller.routed"; }
setup_maintain_worktree() {
  printf '%s\n' "$@" > "$tmp/controller.setup"
  mkdir -p "$5" "$tmp/controller-shim"
  printf '%s' "$tmp/controller-shim"
}
build_prompt() { printf 'prompt\n'; }
cleanup_maintain_worktree() { touch "$tmp/controller.cleaned"; }
log() { :; }

HEAD_A=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
HEAD_B=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
WORK_ROOT="$tmp/controller-work"
SERVER_GH_TOKEN=fake
TIMEOUT_BIN="$tmp/bin/timeout"
GH_BIN="$tmp/bin/gh"
RUNNER="$tmp/bin/controller-runner"
PER_REQUEST_TIMEOUT=30
REVIEW_RUNTIME=legacy
REVIEW_CHILD_GH_TOKEN=
GITHUB_LOGIN=maintainer
ACTIVE_REPO=
ACTIVE_WORKTREE=
ACTIVE_CHILD=
TEST_PR_INFO="{\"headRefOid\":\"$HEAD_B\",\"headRefName\":\"feature/exact\",\"author\":{\"login\":\"maintainer\"}}"
export TEST_PR_INFO
process_one req-1 pr-maintain '{"repo":"owner/repo","pr":42,"url":"https://x/42","title":"x"}' "owner/repo#42@$HEAD_A" run-1 nonce-1
[[ -s "$tmp/controller.superseded" && ! -e "$tmp/controller.setup" ]]

rm -f "$tmp"/controller.{superseded,failed,reconcile,done,routed,cleaned,setup}
TEST_PR_INFO="{\"headRefOid\":\"$HEAD_A\",\"headRefName\":\"feature/exact\",\"author\":{\"login\":\"maintainer\"}}"
export TEST_PR_INFO
process_one req-2 pr-maintain '{"repo":"owner/repo","pr":42,"url":"https://x/42","title":"x"}' "owner/repo#42@$HEAD_A" run-2 nonce-2
trap - RETURN
[[ -s "$tmp/controller.reconcile" && ! -e "$tmp/controller.done" && ! -e "$tmp/controller.routed" && -e "$tmp/controller.cleaned" ]]
sed -n '4p' "$tmp/controller.setup" | grep -Fx 'feature/exact'

awk '/^setup_maintain_worktree\(\) \{/{p=1} p{print} p&&/^\}/{exit}' bin/agent-server > "$tmp/setup-function.sh"
# shellcheck disable=SC1090
. "$tmp/setup-function.sh"
mkdir -p "$tmp/push-bin" "$tmp/fake-clone"
cat > "$tmp/push-bin/git" <<'SH'
#!/usr/bin/env bash
if [[ " $* " == *" ls-remote "* ]]; then
  [[ -z "${REMOTE_LOOKUP_FAIL:-}" ]] || exit 2
  printf '%s\trefs/heads/%s\n' "$REMOTE_HEAD" "$REMOTE_BRANCH"
else
  printf '%s\n' "$*" >> "$TEST_STATE/push-git.calls"
fi
SH
cat > "$tmp/push-bin/timeout" <<'SH'
#!/usr/bin/env bash
shift 2
if [[ "${1:-}" == gh ]]; then printf 'main\n'; exit 0; fi
exec "$@"
SH
chmod +x "$tmp/push-bin/git" "$tmp/push-bin/timeout"

find_local_clone() { printf '%s' "$tmp/fake-clone"; }
git_with_repo_lock() {
  shift
  if [[ "${1:-}" == worktree && "${2:-}" == add ]]; then mkdir -p "$4"; fi
}
setup_github_lease_shim() {
  mkdir -p "$1"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$1/lease-check"
  chmod +x "$1/lease-check"
}
log() { :; }
PATH="$tmp/push-bin:$PATH"
TIMEOUT_BIN="$tmp/push-bin/timeout"
GH_TOKEN=fake
GITHUB_LOGIN=maintainer
TEST_STATE="$tmp"
export PATH GH_TOKEN TEST_STATE
push_shim="$(setup_maintain_worktree owner/repo 42 "$HEAD_A" feature/exact "$tmp/push-worktree" req-3 nonce-3)"

REMOTE_HEAD="$HEAD_A" REMOTE_BRANCH=feature/exact "$push_shim/git" push origin HEAD:feature/exact
REMOTE_HEAD="$HEAD_A" REMOTE_BRANCH=feature/exact "$push_shim/git" push "--force-with-lease=feature/exact:$HEAD_A" origin HEAD:feature/exact
grep -Fq "push https://github.com/owner/repo.git HEAD:feature/exact" "$tmp/push-git.calls"
grep -Fq "push --force-with-lease=feature/exact:$HEAD_A https://github.com/owner/repo.git HEAD:feature/exact" "$tmp/push-git.calls"

reject_push() {
  set +e
  REMOTE_HEAD="${REMOTE_HEAD:-$HEAD_A}" REMOTE_BRANCH="${REMOTE_BRANCH:-feature/exact}" "$push_shim/git" "$@" >/dev/null 2>&1
  local status=$?
  set -e
  [[ "$status" == 3 ]]
}
reject_push push origin HEAD:other
reject_push push upstream HEAD:feature/exact
reject_push push origin HEAD:feature/exact HEAD:other
reject_push push --force origin HEAD:feature/exact
reject_push push --force-with-lease origin HEAD:feature/exact
reject_push push "--force-with-lease=other:$HEAD_A" origin HEAD:feature/exact
reject_push push "--force-with-lease=feature/exact:$HEAD_B" origin HEAD:feature/exact
REMOTE_HEAD="$HEAD_B" reject_push push origin HEAD:feature/exact

head -n 1 Dockerfile.hermes-maintain | grep -Fx 'FROM nousresearch/hermes-agent@sha256:6d7285e1476d0661fc347e3d55245c99decb781d76d39d67582166d2c9561874'
grep -Fq 'bash git gh jq ca-certificates coreutils findutils gawk grep sed util-linux postgresql-client' Dockerfile.hermes-maintain
grep -Fq 'COPY bin/agent-server bin/hermes-maintain-runner.sh ./bin/' Dockerfile.hermes-maintain
grep -Fq 'COPY lib/queue.sh ./lib/queue.sh' Dockerfile.hermes-maintain
if grep -Eq '^COPY (bin/|lib/|agent-config/) \./' Dockerfile.hermes-maintain; then exit 1; fi
grep -Fq 'ENTRYPOINT ["bin/agent-server"]' Dockerfile.hermes-maintain

cp docker-compose.yml .env.example "$tmp/"
cat >> "$tmp/.env.example" <<'EOF'
GH_TOKEN=fake-token
CODE_ROOT=/tmp/code
SWARMVAULT_VAULT=/tmp/vault
HERMES_DOC_API_KEY=0123456789abcdef
EOF
mv "$tmp/.env.example" "$tmp/.env"
docker compose -f "$tmp/docker-compose.yml" --env-file "$tmp/.env" config --format json > "$tmp/compose.json"
jq -e '
  .services["agent-server-maintain"] as $m |
  .services["agent-server-review"] as $r |
  ($m.image == "agent-fleet/hermes-maintain:latest") and
  ($m.build.dockerfile | endswith("Dockerfile.hermes-maintain")) and
  ($m.environment.AGENT_SERVER_RUNNER == "/app/bin/hermes-maintain-runner.sh") and
  (($m.depends_on | keys | sort) == ["db-requests","schema-migrate"]) and
  ([($m.tmpfs // [])[] | select(contains("agent-config"))] | length == 0) and
  ($r.image == "agent-fleet/agent-server:latest") and
  ($r.build.dockerfile | endswith("Dockerfile.agent-server")) and
  ($r.environment.AGENT_SERVER_RUNNER == "/app/bin/mewritecode-runner.sh")
' "$tmp/compose.json" >/dev/null

grep -Fq -- '--force-with-lease=<captured-head-branch>:<claim-sha>' docs/hermes/plan-pr-maintain-cutover.md
grep -Fq 'missing or invalid output reconciles' docs/hermes/plan-pr-maintain-cutover.md
test -f Dockerfile.agent-server
test -f bin/mewritecode-runner.sh
grep -Fq 'Dockerfile.agent-server' docs/hermes/plan-pr-maintain-cutover.md
grep -Fq 'mewritecode-runner.sh' docs/hermes/plan-pr-maintain-cutover.md

echo "PASS: Hermes maintain runner enforces head, branch, push, result, image, and rollback guards"
