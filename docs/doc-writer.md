# Doc-writer fleet: PRD / Design Doc writers

A fleet agent-server that drafts Rokt product/design docs from the status UI, loops the operator in to
answer the doc's open questions, runs a council review, previews exact final bytes, and writes them
to `~/private-docs/inbox` only after explicit Publish approval.

## Flow

```
UI "Documents" form (doc_type + title + requirements)
   → enqueue kind=doc-write (round 1)
doc-writer-server: run the persona (PRD or DD) + bundled Rokt handbook → draft + open_questions JSON
   → open questions?  → human-review queue (answers box + Refine / Finalize / Dismiss)
        Refine (with answers) → re-enqueue round N+1 (folds answers in)   [loop, cap 4 rounds]
        Finalize            → skip remaining questions
   → run council on the draft → append "## Council Review"
   → stage exact final bytes outside private-docs
   → human-review queue: preview bytes + target + digest → Publish / Dismiss
   → Publish writes one no-overwrite <type>-<date>-<slug>.md to ~/private-docs/inbox
      (marked human_reviewed:true because those exact bytes were approved)
```

## Personas
- `agent-config/doc-writer/agents/prd-writer.md` — Rokt Principal PM / Staff Eng. Picks the template
  (SEPRD / MLPRD / Launchpad), enforces North Star metrics + Builder DNA, surfaces unknowns as Open
  Questions instead of chatting.
- `agent-config/doc-writer/agents/dd-writer.md` — Rokt Staff Eng / Architect. Selects SEDD or MLDD,
  mentors through ≥2 options + trade-offs, and enforces E2E ownership, Brain/Suite boundaries,
  paved-road technology, SoR discipline, SLOs, testing, toil reduction, and ADRs.
- `agent-config/doc-writer/handbook/template-sedd.md` and `template-mldd.md` — DD base templates.
- `agent-config/doc-writer/handbook/*` — the Rokt engineering handbook (glossary, standards, templates,
  business context, architecture principles), read-only. Source: go/dev-handbook.

## Safety / boundaries
- **Private-docs write is narrow**: only `~/private-docs/inbox` is mounted WRITABLE (`DOC_WRITER_INBOX_HOST`).
  The rest of the brain is never writable here. Finished docs land in inbox for the human to file.
- **Shared context is read-only**: PRD and DD writers recall prior decisions from Hindsight's
  `fleet-shared` bank and query Coderag for named repositories, Systems, and Components. They cannot
  retain shared memories or modify indexed code.
- Every published doc carries frontmatter `written_by: doc-writer-agent`, `human_reviewed: true`,
  `council_reviewed: <bool>`. Approval is bound to staged path, target, SHA-256 digest, and document
  generation. Nothing is auto-committed.
- **Council degrades gracefully**: if the council skill/infra is unavailable in the container, the doc
  is still written but with an unmissable `⚠ COUNCIL SKIPPED — NOT REVIEWED` banner and
  `council_reviewed: false`. (v1 images do not bundle the council skill; docs are marked accordingly.)
- **Spend cap**: the UI is credential-free (enqueue only); `DOC_WRITE_DAILY_CAP` (default 30) bounds
  doc-write requests per day. The round cap (default 4) bounds the refine loop.
- Slugs are `[a-z0-9-]` truncated. Approval moves work onto request-specific `doc-publish:<id>`
  lineage, so a newer same-title draft cannot supersede approved bytes. Target name is fixed before
  approval. Publication uses `renameat2(RENAME_NOREPLACE)` and never clobbers or picks a new suffix.
- Status runs on an internal network shared only with request Postgres and Hindsight; agent workers
  cannot fetch its CSRF token or invoke Publish. Mutation requests require an allowed Origin.
- A crash after publication preparation stays in `reconcile`. Inspect or finish the exact approved
  target in the doc-writer container with `bin/doc-writer-reconcile <request-id> --inspect`,
  `--complete-matching`, or `--publish-absent`. Recovery never reruns the model or changes target.
- An absent/malformed open-questions block is treated as "needs human", never a silent finalize.

## Run it
`scripts/compose.sh --profile doc-writer up -d --build doc-writer-server` (set `DOC_WRITER_INBOX_HOST` in
`.env`). Then use the **Documents** section of the status UI.

## Optional Signal review alerts

The local `hermes-agent` account can receive a Signal nudge when document questions or exact-byte publication approval enter the review queue. Alerts contain only review ID and event type. Read and approve in the status UI, never in Signal; delivery cannot change review state.

After the normal host-native install creates `DOC_WRITER_STAGE_HOST/alerts`, set `SIGNAL_HOME_CHANNEL=+YOUR_NUMBER` in `/Users/hermes-agent/.hermes/.env` alongside `SIGNAL_ACCOUNT` and `SIGNAL_HTTP_URL=http://127.0.0.1:18080`. As `hermes-agent`, test `hermes send --to signal "Test document alert"` and confirm HTTP 200 from `http://127.0.0.1:18080/api/v1/check`. Opt in through the Compose `.env` with `DOC_ALERT_SPOOL_DIR=/doc-stage/alerts`; recreate `hermes-controller` through the normal fleet rollout and run `sudo scripts/hermes-native.sh doc-alert-start`. `scripts/hermes-native.sh status` shows the timer. Keep Signal credentials and daemon on the host, not in Docker. For a non-default `DOC_WRITER_STAGE_HOST`, export that same path during native install and `doc-alert-start`. Before reinstalling host-native support, run `doc-alert-stop`; reinstall refuses a loaded timer, and `doc-alert-start` loads the new plist afterward.

The host timer checks every minute. If the daemon is down, it retries twice before leaving a `.failed` marker in `DOC_WRITER_STAGE_HOST/alerts`; once healthy, rename it to `.pending` to retry. If delivery is uncertain (including a crash), `.sending` or `.uncertain` **is not retried**: check Note to Self before renaming it to `.pending`. `.sent` prevents duplicate delivery after restart. A review closed just before delivery may get one stale nudge; pending reviews from before activation may also be alerted. The spool uses the existing staff-accessible stage boundary, so another trusted local staff user could forge an ID-only nudge. Retain terminal markers for deduplication; archive them only after the review is no longer pending.

To disable alerts, unset `DOC_ALERT_SPOOL_DIR`, recreate the controller, and run `sudo scripts/hermes-native.sh doc-alert-stop`. Existing markers remain for inspection.

## Known follow-up
- Bundle the council skill into the image so finalized docs get a real council review instead of the
  skipped banner. Until then, run council manually on the drafted doc.
