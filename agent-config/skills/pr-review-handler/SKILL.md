---
name: pr-review-handler
description: "Monitor PR reviews, classify and address feedback (code changes, questions, nits). Distinguishes valid feedback from incorrect bot/human claims. Triggers: handle reviews, PR review, address feedback, reviewer comments, code review, review response, codex review, copilot review, address nits."
group: git-pr
---

# PR Review Handler

Monitor PR review comments, classify each piece of feedback (ACTIONABLE/QUESTION/NITS/APPROVAL/DISCUSSION), and take appropriate action: apply fixes, draft responses, or flag for human decision.

**Not for:** reviewing others' PRs, creating PRs (use publish-branch), general GitHub ops (use `gh`).

## The Process

### Step 1: Fetch Review Comments

Retrieve all review comments for the PR:

```bash
gh pr view <PR_NUMBER> --json reviews,comments,reviewRequests
gh api --paginate repos/{owner}/{repo}/pulls/{pr}/comments
gh api --paginate repos/{owner}/{repo}/pulls/{pr}/reviews
```

Use `--paginate` on the REST calls so PRs with more than one page of inline comments are not truncated — otherwise later comments never participate in the thread join below and their threads stay unclassified.

If no PR number is provided, detect from the current branch:

```bash
gh pr view --json number,reviews,comments
```

Review threads are resolved via GraphQL (not exposed by `gh pr` flags). Fetch thread IDs alongside comments so each can be resolved after it is addressed. Include `fullDatabaseId` on each comment so REST review-comment IDs (from the calls above) map unambiguously to GraphQL thread IDs — matching on `body`/`path` alone is ambiguous when a comment is a reply or two comments share text. Paginate with `--paginate` so PRs with more than 100 threads are not silently truncated:

```bash
gh api graphql --paginate -f query='
  query($owner:String!,$repo:String!,$pr:Int!,$endCursor:String){
    repository(owner:$owner,name:$repo){
      pullRequest(number:$pr){
        reviewThreads(first:100, after:$endCursor){
          pageInfo{ hasNextPage endCursor }
          nodes{
            id isResolved
            comments(first:100){ nodes{ fullDatabaseId body path line } }
          }
        }
      }
    }
  }' -f owner=OWNER -f repo=REPO -F pr=PR_NUMBER
```

Join each REST comment to its thread by `fullDatabaseId` (the REST comment `id`), then resolve that thread's `id`.

### Step 2: Classify Each Comment

Assign each comment to one of these categories:

| Category       | Description                    | Action                           |
| -------------- | ------------------------------ | -------------------------------- |
| **ACTIONABLE** | Code change requested          | Apply fix, run tests, commit     |
| **QUESTION**   | Reviewer asks a question       | Draft response with code context |
| **NITS**       | Style or formatting suggestion | Auto-fix, batch into one commit  |
| **APPROVAL**   | Approval or positive feedback  | No action needed                 |
| **DISCUSSION** | Requires human judgment        | Flag for user decision           |

**Classification signals:**

- "Please rename..." / "Can you change..." / "This should be..." → ACTIONABLE
- "Why did you..." / "What happens if..." / "Can you explain..." → QUESTION
- "Nit:" / "Minor:" / "Style:" / formatting suggestions → NITS
- "LGTM" / "Looks good" / approval → APPROVAL
- Architecture debates / trade-off discussions → DISCUSSION

### Step 3: Present Classification Summary

Show a table of all comments with their classification:

| #   | Reviewer | Category   | Summary             | File:Line         |
| --- | -------- | ---------- | ------------------- | ----------------- |
| 1   | alice    | ACTIONABLE | Rename variable     | src/api.ts:42     |
| 2   | bob      | QUESTION   | Why async here?     | src/service.go:88 |
| 3   | alice    | NITS       | Trailing whitespace | src/util.py:15    |

Interactive mode: ask the user to confirm classifications before proceeding.

Unattended scheduler mode: if the caller explicitly says the run is approved
for unattended/automatic/hourly PR maintenance, do **not** stop for human
classification confirmation. Proceed only on low-risk, unambiguous ACTIONABLE or
NITS items. QUESTION and DISCUSSION items must be reported as drafts/pending
human decisions only; do not post them.

### Step 4: Address Comments by Category

**ACTIONABLE comments:**

1. Read the referenced file and line
2. Understand the requested change in context
3. Apply the change
4. Run related tests to verify no regressions
5. Commit: `fix(review): address <reviewer> feedback - <summary>`

Push and verify (Step 5) before replying or resolving — do not reply to or resolve a thread whose fixing commit is not yet on the remote.

**QUESTION comments:**

1. Analyze the code context around the referenced line
2. Draft a response explaining the design decision or rationale
3. Present the draft to the user for approval before posting
4. Post as a reply on the PR

**NITS comments:**

1. Apply all nit fixes (formatting, naming, whitespace)
2. Batch into a single commit: `style: address review nits`

Push and verify (Step 5) before replying to or resolving the nit threads.

**DISCUSSION comments:**

1. Summarize the discussion thread
2. Present options to the user
3. Wait for user decision before responding

### Step 5: Push First, Then Reply and Resolve

Push the tested commits and confirm the push succeeded **before** replying to or resolving any thread. If the push is rejected (auth, connectivity, non-fast-forward), stop and fix it — never reply to or resolve a thread whose commit reviewers cannot see.

```bash
git push
```

Only after the push lands, reply to each addressed comment, then resolve its thread. Resolve every thread that was addressed (ACTIONABLE, NITS, and any QUESTION answered with an approved reply), using the thread `id` fetched in Step 1:

```bash
gh api graphql -f query='
  mutation($threadId:ID!){
    resolveReviewThread(input:{threadId:$threadId}){
      thread{ id isResolved }
    }
  }' -f threadId=THREAD_ID
```

Do not resolve DISCUSSION threads, or QUESTION threads whose reply the user has not approved — the reviewer resolves those.

Confirm each addressed thread returned `isResolved: true` from the mutation, then present a summary of actions taken.

If files were edited, do not finish with only local uncommitted work. The final
state must be one of:

1. validated fixes committed, pushed, replied to, and resolved;
2. no local diff because validation failed or scope was unsafe (revert your
   attempted changes before reporting); or
3. not pushed because the branch was not pushable, the head moved, a conflict
   remained, or validation failed after edits. In this case, do not reply to or
   resolve comments as fixed, and report the exact blocker.

## Constraints

- **DO** classify all comments before taking any action
- **DO** confirm classifications with the user before proceeding in interactive mode
- **DO** proceed without confirmation only when the caller explicitly grants unattended/automatic PR-maintenance mode
- **DO** run tests after applying ACTIONABLE changes
- **DO** get user approval before posting QUESTION responses
- **DO** batch NITS into a single commit
- **DO** push the fixing commit and confirm it landed before replying to or resolving its thread
- **DO** resolve each thread after it is addressed (reply is not enough)
- **DO** verify addressed threads show `isResolved: true` before reporting
- **DO** end with no local diff, or with validated fixes committed+pushed+replied+resolved
- **DO NOT** leave validated local fixes uncommitted or unpushed
- **DO NOT** auto-respond to DISCUSSION comments without user input
- **DO NOT** resolve DISCUSSION threads, or QUESTION threads with an unapproved reply
- **DO NOT** reply to or resolve a thread whose fixing commit is not yet pushed
- **DO NOT** dismiss review comments without addressing them
- **DO NOT** push changes without running tests first
- **DO NOT** post responses the user hasn't approved

## Output Format

**Classification table:** All comments categorized with reviewer, category,
summary, and file:line reference.

**Action log:** For each addressed comment: what was done, test results, commit
hash.

**Summary:** Comments received (by category), threads resolved count, threads
left open (with reason), pending user decisions, approval status, and final
worktree state (clean / pushed / blocked with reason).
