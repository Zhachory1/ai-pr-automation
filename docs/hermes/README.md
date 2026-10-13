# Hermes design records (historical)

These PRDs and designs describe earlier service-account, request-Postgres, Runs API/controller, and Kanban-bridge migrations. Their startup, installer, rollback, and validation commands **must not be used** for the current personal fleet. The request-DB code and controller are gone; the old data remains in a private offline archive. See [getting started](../getting-started.md), [architecture](../architecture.md), and [operations](../operations.md) for supported behavior.

- [API control-plane PRD](PRD-api-driven-control-plane.md), [design](DD-api-driven-control-plane.md), [delivery plan](plan-api-driven-control-plane.md): superseded request-DB/controller design.
- [PR-safety Kanban grounding](grounding-pr-safety-direct-kanban.md), [PRD](PRD-pr-safety-direct-kanban.md), [design](DD-pr-safety-direct-kanban.md), [council](council-pr-safety-direct-kanban.md), [plan](plan-pr-safety-direct-kanban.md): historical direct-Kanban transition.
- [Pinned CLI evidence](evidence-pr-safety-direct-kanban-cli.json): historical test evidence, not a current runtime check.

Current PR discovery submits through signed host ingress into Hermes Kanban; GitHub holds PR heads, feedback, and effects. No request database, controller, or request-DB rollback is part of the supported runtime. Hindsight's separate Postgres-backed memory store remains a support service.
