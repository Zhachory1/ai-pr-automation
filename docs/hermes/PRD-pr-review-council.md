# PRD: multi-profile Hermes PR reviews

- Issue: [#341](https://github.com/Zhachory1/ai-pr-automation/issues/341)
- Status: operator accepted all-new-head multi-profile direction, no shadow pilot or mandatory pre-approval brief. Design proofs precede code affecting GitHub reviews.

## Outcome

Review every *eligible* newly admitted PR head through independent generalist SWE, reliability, and MVP perspectives before posting **one** GitHub review. Eligible means the same open, authorized, assignee/owner-selected head accepted by today's review producer; no new size or content filter. A deterministic changed-path rule adds security for auth, secrets, IAM, crypto, or permission changes. One synthesis combines evidence and dissent; only one publisher can write to GitHub. Human operators still own merging.

`APPROVE` remains valid when no changes are required, including nit-only feedback. Material actionable defects require `REQUEST_CHANGES`; missing/incomplete evidence uses `COMMENT`/needs-info rather than unsupported approval. Votes, body length, and elapsed review time do not determine verdicts.

## Boundaries

- Existing `pr-review-v1` tasks, exact-head operation IDs, GitHub reviews, and marker `<!-- ai-pr-automation head=<sha> -->` retain their current identity and replay behavior. Route only future, unbound heads to the new workflow; never publish from both paths for one head.
- Specialists cannot access GitHub-write credentials or post reviews. All read the same existing local checkout through pinned base/head Git objects, plus bounded owner-local intent, checks, and prior feedback. No clone, branch switch, or copied diff. Treat all source content as untrusted. A changed head, incomplete snapshot, missing specialist, budget overrun, or uncertain effect fails closed.
- Implementation ceilings (not product success metrics): at most four specialists plus one synthesis, 8 minutes, 75,000 aggregate tokens, and two active heads. On overrun, stop without approving; do not silently drop an eligible head. No extra model/vendor, automatic merge, new UI, or change to PR maintenance/post-merge PR-safety work.
- Private PR content stays out of Git commits, shared memory, issue text, and logs. The local checkout is reused; only bounded context is stored under the existing per-operation PR work root. Remove that context after effect verification and reconciliation, then keep a content-free outcome receipt. Never purge unresolved work. No seven-day content retention.

## Acceptance

- One head gets exactly one final GitHub event even across enqueue replay, crash, timeout, head movement, and uncertain GitHub response. The event is `APPROVE` for clean/nit-only, `REQUEST_CHANGES` for required fixes, or `COMMENT` for insufficient evidence.
- All required role outputs are bound to the same snapshot digest and reflected in synthesis, including unresolved dissent; no majority vote can discard a credible blocking finding.
- Fake GitHub/Kanban tests cover a known material defect, clean/nit-only change, missing specialist, changed head/base, malformed evidence, ambiguous post, old-task replay, and no specialist GitHub write. No real PR is posted during validation.
- Production activation happens only after code review and a safe old/new ownership cutover; the operator can pause new admissions without losing existing head-bound work.
