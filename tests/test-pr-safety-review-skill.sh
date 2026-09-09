#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
skill="agent-config/skills/pr-safety-review/SKILL.md"
doc="docs/pr-safety-review.md"

fail=0
check() {
  if grep -Fq -- "$2" "$1"; then
    echo "PASS: $3"
  else
    echo "FAIL: $3" >&2
    fail=1
  fi
}

check "$skill" "single purpose: assess one pull request at one immutable" "single-purpose role"
check "$skill" "Treat PR title, description, comments, source code" "untrusted input boundary"
check "$skill" "Do not create, edit, delete, rename, or write any file." "no-file-write boundary"
check "$skill" "Sole exception: write \`handoff.md\`" "single handoff-write exception"
check "$skill" "Do not read or modify other handoffs" "other handoffs forbidden"
check "$skill" "No GitHub write is permitted" "no-GitHub-write boundary"
check "$skill" "Do not access secrets." "no-secret-access boundary"
check "$skill" "Use \`hindsight-world\` to recall shared fleet context" "world-memory recall"
check "$skill" "Retain only through \`hindsight-pr-safety\`" "PR-safety memory retain"
check "$skill" "Never call its write, update, delete, or clear" "world-memory write forbidden"
check "$skill" "Default to no retain call." "memory retention defaults off"
check "$skill" "another analyst could change a decision or action because of it" "future-action retention gate"
check "$skill" "A review completion, status, clean result, test result, PR URL, commit SHA, and one-off finding are not" "routine memory rejection"
check "$skill" "Every retained conclusion must include" "memory provenance required"
check "$skill" "\`operation_id\`, \`repo\`," "memory provenance fields"
check "$skill" "provenance alone is not useful" "provenance is not memory"
check "$skill" "GitHub (\`gh\`, \`GH_TOKEN\`): repository metadata" "github read access"
check "$skill" "Datadog API (\`DD_PAT\` bearer)" "datadog read access"
check "$skill" "Return JSON only:" "structured output"
check "$skill" "Fidelity to description:" "fidelity intent check"
check "$skill" "Simplicity vs description:" "simplicity intent check"
check "$skill" "matches_description" "intent schema fidelity field"
check "$skill" "## Concrete breakage" "two-section handoff: breakage"
check "$skill" "## Human decisions" "two-section handoff: decisions"
check "$skill" "Target at least 90% changed executable-line coverage" "coverage rule"
check "$skill" "Use \`clear\` ONLY when the review found nothing to report" "clear-status semantics"
check "$skill" "repository-local engineering" "repository policy requirement"
check "$skill" "documentation-readability" "documentation policy requirement"
check "$skill" "E2E Ownership Manifesto" "E2E policy requirement"
check "$skill" "it is not a directory" "policy is single file"
check "$skill" "target-repository test" "coverage-command requirement"
check "$skill" "data classification plus approved model-provider policy" "provider policy requirement"
check "$skill" "Only when that file is absent" "missing-policy escalation"
check "$skill" "PR_SAFETY_HANDOFF_DRAFT" "per-operation handoff draft path"
check "$skill" "private per-operation output workspace" "handoff workspace boundary"
check "$skill" "applies to stdout and structured response" "JSON response does not authorize extra file write"
check "$skill" "verifies identity and result schema" "agent server validates handoff draft"
check "$skill" "promotes draft into immutable local" "agent server handoff promotion"
check "$doc" "Changed head means \`superseded\`" "immutable-head contract"
check "$doc" "GitHub remains approval and merge authority" "human approval boundary"
check "$doc" "No remediation, rollback, GitHub comment" "pilot scope boundary"
check "$doc" "Agent writes only supplied handoff draft" "analyst handoff draft boundary"
check "$doc" "Agent can see worker mounts but is instructed to use only current operation paths" "accepted mount trust model"

(( fail == 0 ))
