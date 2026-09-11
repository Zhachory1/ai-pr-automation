# Doc-writer fleet: PRD / Design Doc writers

A fleet agent-server that drafts Rokt product/design docs from the status UI, loops the operator in to
answer the doc's open questions, runs a council review, and writes the finished doc to
`~/private-docs/inbox`.

## Flow

```
UI "Documents" form (doc_type + title + requirements)
   → enqueue kind=doc-write (round 1)
doc-writer-server: run the persona (PRD or DD) + bundled Rokt handbook → draft + open_questions JSON
   → open questions?  → human-review queue (answers box + Refine / Finalize / Dismiss)
        Refine (with answers) → re-enqueue round N+1 (folds answers in)   [loop, cap 4 rounds]
        Finalize            → skip remaining questions
   → run council on the draft → append "## Council Review"
   → write <type>-<date>-<slug>.md to ~/private-docs/inbox  (marked human_reviewed:false)
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
- Every written doc carries frontmatter `written_by: doc-writer-agent`, `human_reviewed: false`,
  `council_reviewed: <bool>`. Nothing is auto-committed.
- **Council degrades gracefully**: if the council skill/infra is unavailable in the container, the doc
  is still written but with an unmissable `⚠ COUNCIL SKIPPED — NOT REVIEWED` banner and
  `council_reviewed: false`. (v1 images do not bundle the council skill; docs are marked accordingly.)
- **Spend cap**: the UI is credential-free (enqueue only); `DOC_WRITE_DAILY_CAP` (default 30) bounds
  doc-write requests per day. The round cap (default 4) bounds the refine loop.
- Slugs are `[a-z0-9-]` truncated; existing inbox files are never clobbered (suffix `-2`, `-3`).
- An absent/malformed open-questions block is treated as "needs human", never a silent finalize.

## Run it
`docker compose --profile doc-writer up -d --build doc-writer-server` (set `DOC_WRITER_INBOX_HOST` in
`.env`). Then use the **Documents** section of the status UI.

## Known follow-up
- Bundle the council skill into the image so finalized docs get a real council review instead of the
  skipped banner. Until then, run council manually on the drafted doc.
