# Documentation

- **[From-zero host-native setup](getting-started.md)** — prerequisites, credentials, TLS, and explicit live-activation gate.
- **[Configuration](configuration.md)** — current credentials and process boundaries.
- **[Architecture](architecture.md)** — current GitHub → Kanban → Hermes PR flow.
- **[Historical documentation plan](documentation-plan.md)** — superseded rollout checklist.
- **[Operations](operations.md)** — Kanban status, reconciliation, safe stop, and recovery limits.
- **[Contributing](contributing.md)** — source map and focused checks.
- **[Compose substrate](../docker/README.md)** — current container contracts. **[Historical Hermes runtime](hermes/README.md)** — retired controller/Runs API design.

## Historical design records

- **[hermes-migration-roadmap.md](hermes-migration-roadmap.md)** — proposed strangler migration
  from custom fleet plumbing to Hermes while retaining domain safety controls.
- **[hermes/](hermes/)** — grounded M0–M2 PRD/DD and M0 evidence-scaffold implementation plan.
- **[roadmap.md](roadmap.md)** — historical agent pub/sub orchestration roadmap (M0→M4). Mirrored
  from the design vault; where it differs from shipped code, `m0/DD.md` and code are authoritative.
- **[m0/](m0/)** — the M0 (compose substrate) run: `PRD.md`, `DD.md`, `PLAN.md`. Produced by the
  plan-to-launch workflow for the substrate delivered in `docker-compose.yml` + `docker/`.
- **[pr-safety-review.md](pr-safety-review.md)**, **[decision-approval.md](decision-approval.md)**, **[memory-curation.md](memory-curation.md)**, and **[doc-writer.md](doc-writer.md)** — retired request-DB workflows; not activation guides.
