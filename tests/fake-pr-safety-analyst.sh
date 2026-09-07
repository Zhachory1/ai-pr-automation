#!/usr/bin/env bash
set -euo pipefail
: "${PR_SAFETY_SNAPSHOT_PATH:?}"; : "${PR_SAFETY_POLICY_PATH:?}"
: "${PR_SAFETY_HANDOFF_DRAFT:?}"; : "${PR_SAFETY_RESULT_FILE:?}"
# Analyst may hold read-only investigation creds (GH_TOKEN/BUILDKITE_API_TOKEN/DD_PAT) after PR #38,
# but must never receive DB or write-path credentials.
[[ -z "${PGPASSWORD:-}" && -z "${REQUESTS_DB_HOST:-}" && -z "${REQUESTS_DB_PASSWORD:-}" ]] || exit 9

identity="$(grep -m1 '^{' "${1:?prompt file}")"
op="$(jq -r .operation_id <<<"$identity")"
status="${TEST_PR_SAFETY_RESULT_STATUS:-clear}"
case "$op" in op-success|op-invalid-handoff|op-paraphrase) status=changes_requested ;; esac
grep -qx changes_requested "$PR_SAFETY_POLICY_PATH" && status=changes_requested || true
# op-paraphrase models real LLM behavior: a readable handoff that neither embeds the identity JSON
# nor byte-copies finding claims/evidence from result.json. The controller must stamp identity and
# accept it (identity/findings integrity come from the header + validated result.json, not prose).
if [[ "$op" == op-paraphrase ]]; then
  printf '# PR safety handoff\n\n**Status:** %s\n\nReviewer reworded the findings in prose here.\n' "$status" > "$PR_SAFETY_HANDOFF_DRAFT"
elif [[ "$op" != op-invalid-handoff ]]; then
  printf '# PR safety handoff\nStatus: %s\n%s\n' "$status" "$identity" > "$PR_SAFETY_HANDOFF_DRAFT"
fi
if [[ "$op" == op-invalid-result ]]; then
  printf '{}\n' > "$PR_SAFETY_RESULT_FILE"
else
  jq --arg status "$status" '. + {status:$status,intent:{claimed:"",evidence:[],needed:"unknown",smaller_existing_solution:null},findings:[],coverage:{status:"unavailable",command:null,changed_executable_line_coverage_percent:null,gaps:[]},documentation:{status:"not_applicable",required_updates:[]},observability:{status:"not_applicable",recommended_metrics:[],recommended_slos_or_runbooks:[],datadog_terraform_candidate:false},incident:{candidate:false,failure_mode:null,blast_radius:null,recommended_action:null,evidence:[]},human_decisions_needed:[]}' <<<"$identity" > "$PR_SAFETY_RESULT_FILE"
  if [[ "$op" == op-clear-with-finding ]]; then
    jq '.findings = [{severity:"minor",category:"test",claim:"clear must not discard this",evidence:[{source:"test",detail:"fixture"}],risk:"finding lost",recommended_remediation:"write handoff",confidence:"high"}]' "$PR_SAFETY_RESULT_FILE" > "$PR_SAFETY_RESULT_FILE.tmp"
    mv "$PR_SAFETY_RESULT_FILE.tmp" "$PR_SAFETY_RESULT_FILE"
  fi
fi
