# Documentation

- **[From-zero host-native setup](getting-started.md)** — prerequisites, credentials, TLS, and explicit live-activation gate.
- **[Documentation rollout plan](documentation-plan.md)** — implementation status and remaining clean-host trial.
- **[Configuration](configuration.md)** — credentials, ownership, host/Compose precedence, and scope switches.
- **[Architecture](architecture.md)** — control plane, trust boundaries, and effects by workflow.
- **[Operations](operations.md)** — status, queue inspection, reconciliation, safe stop, and recovery limits.
- **[Contributing](contributing.md)** — source map and focused checks.
- **[Hermes runtime](hermes/README.md)** and **[Compose substrate](../docker/README.md)** — deeper current contracts.

## Historical design records

- **[hermes-migration-roadmap.md](hermes-migration-roadmap.md)** — proposed strangler migration
  from custom fleet plumbing to Hermes while retaining domain safety controls.
- **[hermes/](hermes/)** — grounded M0–M2 PRD/DD and M0 evidence-scaffold implementation plan.
- **[roadmap.md](roadmap.md)** — historical agent pub/sub orchestration roadmap (M0→M4). Mirrored
  from the design vault; where it differs from shipped code, `m0/DD.md` and code are authoritative.
- **[m0/](m0/)** — the M0 (compose substrate) run: `PRD.md`, `DD.md`, `PLAN.md`. Produced by the
  plan-to-launch workflow for the substrate delivered in `docker-compose.yml` + `docker/`.
- **[pr-safety-review.md](pr-safety-review.md)** — merged-PR producer and dedicated PR-safety agent
  server. Agent runs directly in worker container; only dedicated PR-safety memory is writable.
