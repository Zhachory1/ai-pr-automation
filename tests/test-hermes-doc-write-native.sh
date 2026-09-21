#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/stage" "$tmp/inbox" "$tmp/home/.hermes/profiles"
cp -R agent-config/hermes/profiles/doc-write-v1 "$tmp/home/.hermes/profiles/"

cat > "$tmp/bin/psql" <<SH
#!/usr/bin/env bash
query="\$*"; echo "\$RUN_CASE \$query" >> "$tmp/db.log"
case "\$query" in
  *"hermes_claim_request"*)
    case "\$RUN_CASE" in
      open) jq -cn '{id:41,created_at:"2026-09-21T10:00:00Z",payload:{doc_type:"dd",title:"Questions",requirements:"Need decisions",round:1}}' ;;
      final) jq -cn '{id:42,created_at:"2026-09-21T10:00:00Z",payload:{doc_type:"dd",title:"Native Doc",requirements:"Write it",round:1,finalize:true}}' ;;
      publication) jq -cn '{id:42,created_at:"2026-09-21T10:00:00Z",payload:{doc_type:"dd",title:"Native Doc",requirements:"Write it",round:1,finalize:true,publication_only:true}}' ;;
    esac ;;
  *"hermes_doc_publication_claimed"*|*"hermes_prepare_doc_publication"*)
    jq -cn --arg d "$(printf 'final bytes' | shasum -a 256 | awk '{print $1}')" \
      '{staged_path:"requests/42/publish.md",target_path:"dd-2026-09-21-native-doc-42.md",content_digest:\$d,document_generation:"hermes:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' ;;
  *"hermes_settle_doc_questions"*|*"hermes_stage_doc_publication"*|*"hermes_mark_doc_published"*|*"hermes_settle_request"*) echo t ;;
  *"hermes_renew_request"*) echo t ;;
esac
SH
cat > "$tmp/bin/hermes" <<SH
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$tmp/hermes.args"
if env | grep -Eq '^(PGPASSWORD|REQUESTS_DB_|DOC_WRITER_INBOX_DIR)='; then touch "$tmp/env-leak"; exit 9; fi
query=""; while ((\$#)); do [[ "\$1" != --query-file ]] || { query="\$2"; shift; }; shift; done
nonce="\$(grep -Eo '[0-9a-f]{32}' "\$query" | head -1)"
if grep -q '\\"title\\":\\"Questions\\"' "\$query"; then
  jq -cn --arg n "\$nonce" '{detail:"needs input",draft:"draft bytes",nonce:\$n,open_questions:["Which option?"],status:"open_questions"}'
else
  jq -cn --arg n "\$nonce" '{detail:"ready",document:"final bytes",nonce:\$n,status:"final"}'
fi
SH
chmod +x "$tmp/bin/psql" "$tmp/bin/hermes"

common=(PATH="$tmp/bin:$PATH" HOME="$tmp/home" HERMES_BIN="$tmp/bin/hermes"
  DOC_WRITER_PROFILE_DIR="$tmp/home/.hermes/profiles/doc-write-v1"
  DOC_WRITER_STAGE_DIR="$tmp/stage" DOC_WRITER_INBOX_DIR="$tmp/inbox"
  DOC_WRITER_PUBLICATION_BIN="$PWD/bin/doc-writer-publication" REQUESTS_DB_USER=test PGPASSWORD=secret)

env RUN_CASE=open "${common[@]}" bin/hermes-doc-write-runner | grep -q 'status=open_questions'
[[ "$(cat "$tmp/stage/requests/41/draft.md")" == 'draft bytes' ]]
grep -q 'hermes_settle_doc_questions' "$tmp/db.log"

env RUN_CASE=final "${common[@]}" bin/hermes-doc-write-runner | grep -q 'status=awaiting_approval'
[[ "$(cat "$tmp/stage/requests/42/publish.md")" == 'final bytes' ]]
[[ ! -e "$tmp/inbox/dd-2026-09-21-native-doc-42.md" ]]
grep -q 'hermes_stage_doc_publication' "$tmp/db.log"
[[ ! -e "$tmp/env-leak" ]]
grep -Fxq -- '--toolsets' "$tmp/hermes.args"; grep -Fxq no_mcp "$tmp/hermes.args"
if grep -Eq '^(terminal|file)$' "$tmp/hermes.args"; then
  echo 'FAIL: model received a file or terminal toolset' >&2; exit 1
fi

env RUN_CASE=publication "${common[@]}" bin/hermes-doc-write-runner | grep -q 'status=done'
[[ "$(cat "$tmp/inbox/dd-2026-09-21-native-doc-42.md")" == 'final bytes' ]]
prepare_line="$(grep -n 'hermes_prepare_doc_publication' "$tmp/db.log" | tail -1 | cut -d: -f1)"
mark_line="$(grep -n 'hermes_mark_doc_published' "$tmp/db.log" | tail -1 | cut -d: -f1)"
(( prepare_line < mark_line ))

# Exact digest, symlink rejection, and no-overwrite behavior are enforced by the native helper.
printf original > "$tmp/exact"
if printf changed | bin/doc-writer-publication stage --stage-root "$tmp/stage" --request-id 42 --name publish.md >/dev/null 2>&1; then
  echo 'FAIL: changed staged bytes replaced immutable stage' >&2; exit 1
fi
mkdir "$tmp/symlink-stage"; ln -s "$tmp/stage/requests" "$tmp/symlink-stage/requests"
if bin/doc-writer-publication read --stage-root "$tmp/symlink-stage" --staged-path requests/42/publish.md >/dev/null 2>&1; then
  echo 'FAIL: symlinked stage hierarchy accepted' >&2; exit 1
fi
printf keep > "$tmp/inbox/dd-2026-09-21-existing-9.md"
digest="$(printf 'final bytes' | shasum -a 256 | awk '{print $1}')"
if bin/doc-writer-publication publish --stage-root "$tmp/stage" --inbox-root "$tmp/inbox" \
  --staged-path requests/42/publish.md --target-path dd-2026-09-21-existing-9.md --digest "$digest" >/dev/null 2>&1; then
  echo 'FAIL: existing target accepted' >&2; exit 1
fi
[[ "$(cat "$tmp/inbox/dd-2026-09-21-existing-9.md")" == keep ]]
ln -s "$tmp/exact" "$tmp/inbox/dd-2026-09-21-symlink-10.md"
if bin/doc-writer-publication publish --stage-root "$tmp/stage" --inbox-root "$tmp/inbox" \
  --staged-path requests/42/publish.md --target-path dd-2026-09-21-symlink-10.md --digest "$digest" >/dev/null 2>&1; then
  echo 'FAIL: symlink publication target accepted' >&2; exit 1
fi
[[ -L "$tmp/inbox/dd-2026-09-21-symlink-10.md" ]]

echo 'PASS: native doc-write stages strict results and publishes approved exact bytes without overwrite'
