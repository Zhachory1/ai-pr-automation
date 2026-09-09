#!/usr/bin/env bash
set -euo pipefail
: "${PR_SAFETY_SNAPSHOT_PATH:?}"; : "${PR_SAFETY_POLICY_PATH:?}"
: "${PR_SAFETY_HANDOFF_DRAFT:?}"; : "${PR_SAFETY_RESULT_FILE:?}"
# Analyst may hold read-only investigation creds (GH_TOKEN/BUILDKITE_API_TOKEN/DD_PAT) after PR #38,
# but must never receive DB or write-path credentials.
[[ -z "${PGPASSWORD:-}" && -z "${REQUESTS_DB_HOST:-}" && -z "${REQUESTS_DB_PASSWORD:-}" ]] || exit 9
mkdir -p "$MEWRITE_CODING_AGENT_DIR/sessions"
touch "$MEWRITE_CODING_AGENT_DIR/sessions/fake-session" "$MEWRITE_CODING_AGENT_DIR/sessions/.hidden-session"

args=("$@")
cwd=""
for ((i=0; i<${#args[@]}; i++)); do
  [[ "${args[i]}" == --cwd ]] && cwd="${args[i+1]}"
done
[[ -n "$cwd" && "$cwd" != "$PR_SAFETY_SNAPSHOT_PATH" && -L "$cwd/snapshot" ]] || exit 8
prompt="${!#}"
identity="$(grep -m1 '^{' <<<"$prompt")"
op="$(jq -r .operation_id <<<"$identity")"
status="${TEST_PR_SAFETY_RESULT_STATUS:-clear}"
case "$op" in op-success|op-invalid-result-schema|op-invalid-handoff|op-paraphrase) status=changes_requested ;; esac
grep -qx changes_requested "$PR_SAFETY_POLICY_PATH" && status=changes_requested || true
# op-paraphrase models real LLM behavior: a readable handoff that neither embeds the identity JSON
# nor byte-copies finding claims/evidence from result.json. The agent server must stamp identity and
# accept it (identity/findings integrity come from the header + validated result.json, not prose).
if [[ "$op" == op-paraphrase ]]; then
  printf '# PR safety handoff\n\n**Status:** %s\n\n## Concrete breakage\n\nReviewer reworded the findings in prose here.\n\n## Human decisions\n\nNone.\n' "$status" > "$PR_SAFETY_HANDOFF_DRAFT"
elif [[ "$op" == op-invalid-handoff ]]; then
  printf '# PR safety handoff\nStatus: %s\n%s\nMissing required sections.\n' "$status" "$identity" > "$PR_SAFETY_HANDOFF_DRAFT"
else
  printf '# PR safety handoff\nStatus: %s\n%s\n\n## Concrete breakage\n\nNone.\n\n## Human decisions\n\nNone.\n' "$status" "$identity" > "$PR_SAFETY_HANDOFF_DRAFT"
fi
if [[ "$op" == op-invalid-result ]]; then
  printf '{}\n' > "$PR_SAFETY_RESULT_FILE"
else
  jq --arg status "$status" '. + {status:$status,intent:{claimed:"",evidence:[],needed:"unknown",smaller_existing_solution:null,matches_description:"unknown",description_divergence:null,simpler_alternative:null},findings:[],coverage:{status:"unavailable",command:null,changed_executable_line_coverage_percent:null,gaps:[]},documentation:{status:"not_applicable",required_updates:[]},observability:{status:"not_applicable",recommended_metrics:[],recommended_slos_or_runbooks:[],datadog_terraform_candidate:false},incident:{candidate:false,failure_mode:null,blast_radius:null,recommended_action:null,evidence:[]},human_decisions_needed:[]}' <<<"$identity" > "$PR_SAFETY_RESULT_FILE"
  if [[ "$op" == op-clear-with-finding ]]; then
    jq '.findings = [{severity:"minor",category:"tests",file:null,line:null,claim:"clear must not discard this",evidence:[{source:"test",detail:"fixture"}],risk:"finding lost",recommended_remediation:"write handoff",confidence:"high"}]' "$PR_SAFETY_RESULT_FILE" > "$PR_SAFETY_RESULT_FILE.tmp"
    mv "$PR_SAFETY_RESULT_FILE.tmp" "$PR_SAFETY_RESULT_FILE"
  elif [[ "$op" == op-invalid-result-schema ]]; then
    jq '.findings = [{severity:"minor",category:"unknown",file:7,line:"ten",claim:"bad schema",evidence:[{source:"test",detail:"fixture"}],risk:"bad result accepted",recommended_remediation:"reject",confidence:"high"}]' "$PR_SAFETY_RESULT_FILE" > "$PR_SAFETY_RESULT_FILE.tmp"
    mv "$PR_SAFETY_RESULT_FILE.tmp" "$PR_SAFETY_RESULT_FILE"
  fi
fi
