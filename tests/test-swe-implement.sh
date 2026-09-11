#!/usr/bin/env bash
# Unit tests for the swe-implement harness's input handling and guards. Does NOT run the agent, clone,
# or push (those need live creds + a model); it exercises arg parsing, input normalization, and the
# precondition failures that must stop before any network or write action.
set -uo pipefail
cd "$(dirname "$0")/.."
H=bin/swe-implement
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fail=0
# expect_out <name> <regex> -- <cmd...>: run cmd, assert combined output matches regex
expect_out() {
  local name="$1" re="$2"; shift 3
  local out; out="$("$@" 2>&1 || true)"
  if grep -qE "$re" <<<"$out"; then echo "PASS: $name"; else echo "FAIL: $name" >&2; echo "   got: $out" >&2; fail=1; fi
}

mkdir -p "$TMP/agents"; printf -- '---\nname: x\n---\nbody\n' > "$TMP/agents/swe.md"
export GH_TOKEN=fake SWE_IMPLEMENTER_AGENT="$TMP/agents/swe.md"

expect_out "no input source is rejected"        'one of --handoff'        -- bash "$H"
expect_out "prompt without repo is rejected"    'requires --repo'         -- bash "$H" --prompt 'do x'
expect_out "bad issue format is rejected"       'owner/repo#N'            -- bash "$H" --issue 'ROKT/cpi-9'
expect_out "missing agent def is rejected"      'missing agent definition' -- env SWE_IMPLEMENTER_AGENT=/nope.md bash "$H" --prompt x --repo ROKT/cpi

# missing GH_TOKEN (unset it just for this call)
out="$(env -u GH_TOKEN SWE_IMPLEMENTER_AGENT="$TMP/agents/swe.md" bash "$H" --prompt x --repo ROKT/cpi 2>&1 || true)"
if grep -qi 'GH_TOKEN' <<<"$out"; then echo "PASS: missing GH_TOKEN is rejected"; else echo "FAIL: missing GH_TOKEN is rejected" >&2; fail=1; fi

# handoff with empty concrete-breakage is rejected
cat > "$TMP/none.md" <<'EOF'
<!-- pr-safety identity
repo: ROKT/cpi
pr: 7
base_sha: abc123
-->
## Concrete breakage

None.

## Human decisions

- decide something
EOF
expect_out "handoff with empty breakage is rejected" 'no .*Concrete breakage' -- bash "$H" --handoff "$TMP/none.md"

# handoff without a valid repo identity is rejected
printf '## Concrete breakage\n- fix a thing\n' > "$TMP/norepo.md"
expect_out "handoff without repo identity is rejected" 'no valid repo identity' -- bash "$H" --handoff "$TMP/norepo.md"

# Agent's concrete commit message drives human-facing PR metadata; source-specific text is fallback.
if grep -q 'commit_title="$(git log -1' "$H" \
   && grep -q 'commit_body="$(git log -1' "$H" \
   && grep -q 'title="${commit_title:-' "$H" \
   && grep -q "title=.\$(printf '%s' .\$title. | cut -c1-72)" "$H" \
   && grep -q 'summary="${commit_body:0:8000}"' "$H"; then
  echo 'PASS: PR title and summary come from agent commit metadata'
else
  echo 'FAIL: concrete PR metadata derivation missing' >&2; fail=1
fi

if grep -q 'draft PR review request:' "$H"; then
  echo 'PASS: created PR emits a typed pr-review request'
else
  echo 'FAIL: pr-review request output missing' >&2; fail=1
fi

# #95: gh pr create output must not be contaminated by stderr, and a created-PR-with-unparseable-URL
# must be a hard failure (never a soft WARN that ships an un-reviewed PR).
if grep -qE 'gh pr create .*2>"\$gherr"' "$H" && ! grep -qE 'url="\$\(gh pr create.*2>&1\)"' "$H"; then
  echo 'PASS: gh pr create captures stdout only (stderr separated)'
else
  echo 'FAIL: gh pr create still mixes stderr into the URL (2>&1)' >&2; fail=1
fi
if grep -q 'draft PR created but its URL could not be parsed' "$H" \
   && ! grep -q 'WARN: could not build pr-review request' "$H"; then
  echo 'PASS: unparseable created-PR URL is a hard failure, not a soft WARN'
else
  echo 'FAIL: created PR with no parseable URL is not failing hard' >&2; fail=1
fi
if grep -q "grep -oE 'https://github" "$H"; then
  echo 'PASS: PR URL is robustly extracted (grep), not an exact whole-output match'
else
  echo 'FAIL: PR URL extraction not robust' >&2; fail=1
fi

(( fail == 0 ))
