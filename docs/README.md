# Design docs

- **[roadmap.md](roadmap.md)** — the agent pub/sub orchestration roadmap (M0→M4). Mirrored from
  the design vault; where it differs from shipped code, `m0/DD.md` and the code are authoritative.
- **[m0/](m0/)** — the M0 (compose substrate) run: `PRD.md`, `DD.md`, `PLAN.md`. Produced by the
  plan-to-launch workflow for the substrate delivered in `docker-compose.yml` + `docker/`.
- **[pr-safety-review.md](pr-safety-review.md)** — draft-only immutable-SHA PR safety controller
  and isolated analyst runner. No producer or external write path exists.
