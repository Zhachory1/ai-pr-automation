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

# PR title is human-facing (repo#pr), not the raw handoff filename / source_ref. Assert the harness
# builds a pr_title per input mode and uses it (falling back to source_ref only if unset).
if grep -q 'pr_title="swe-implement(${repo##\*/}#${local_pr:-?}):' "$H" \
   && grep -q 'title="${pr_title:-swe-implement: ${source_ref}}"' "$H"; then
  echo 'PASS: PR title derived from repo#pr, not filename'
else
  echo 'FAIL: PR title derivation missing' >&2; fail=1
fi

(( fail == 0 ))
