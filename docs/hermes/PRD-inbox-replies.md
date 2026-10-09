# PRD: Hermes Gmail Drafts

- Status: operator approved Gmail Drafts for manual sending. Supervised pilot ran; owner LaunchAgent is enabled for 08:00 New York. No automatic email send exists.
- Pilot account: `zhachory1@gmail.com`. Additional account `zhackymoto@gmail.com` is opt-in after separate local authorization; linked [design](DD-inbox-replies.md) and [remaining work](PLAN-inbox-replies.md).

## Outcome

Luna triages the preceding New York calendar day's read **and** unread INBOX messages. Eligible recruiter/job and help/mentorship/meeting requests become deduplicated Hermes Kanban cards. Sol drafts from those cards. The local controller places the reviewed reply in **Gmail Drafts**; the operator edits or sends it in Gmail. No Kanban comment, Ready move, or model action sends an email.

## Scope

- Run at 08:00 `America/New_York` for midnight-to-midnight yesterday; no automatic historical backfill. Cap each day at 250 unique listed INBOX IDs. Every ID without a completed workflow decision may reach Luna, regardless of Gmail's read flag. Overflow and missed days require visible Kanban notices, not silent truncation.
- Luna (`gpt-5.6-luna`) routes plausible staff/principal AI/ML or Director of Engineering inquiries and personal help/time requests. Unknown job terms become questions for Sol, not inferred rejections. Unrelated mail is skipped.
- Sol (`gpt-5.6-sol`) reads only an admitted current thread and bounded private voice context. It drafts without inventing career claims, compensation, or availability. The controller—not either model—selects account, recipient, thread, and Gmail Draft destination.
- Keep original mail and private voice files out of Git and shared memory. Store only projected intake metadata and generated reply previews on the local board; keep MIME, OAuth credentials, and effect state in owner-only local storage. The operator accepts profile-local Hermes session retention.

## Guardrails

- Zero calls to Gmail send or label/modify APIs. `gmail.compose` technically permits sending, but the draft controller uses only `drafts.create`, `drafts.get`, and read-only thread/profile calls. The operator alone presses **Send** in Gmail.
- Bind each draft to the verified account, one recipient, thread, current source-message fingerprint, immutable MIME/digest, and staged card version. Recheck source freshness before draft creation. Do not automatically retry an uncertain create: retain the known draft ID when available and reconcile by readback.
- Deduplicate intake by account + Gmail message ID. Persist admitted **and** skipped decisions only after handling succeeds; manual same-date replay skips completed IDs and uses the same Kanban key. A later run can report missed days, but cannot report its own absence if it never starts.
- Do not follow links, fetch attachments, book meetings, change read state, or process LinkedIn DMs in this slice. Exclude customer/work account integration; operator confirmed provider/data authority for forwarded material already present in the personal INBOX.

## Pilot and success

The supervised pilot scanned one prior-day INBOX: 37 messages triaged, 35 skipped, 2 admitted and drafted. One Gmail Draft was created and verified; the other staged card was archived before migration and was **not** reopened or drafted in Gmail. No email was sent. The operator reviewed the draft and a synthetic live failure-notice drill passed before enabling the 08:00 New York LaunchAgent. Monitor its first scheduled run. Proposed two-week measure: at least 90% of operator-labeled eligible messages receive one inspectable Gmail Draft by the next morning, with zero duplicate draft creations or API sends.
