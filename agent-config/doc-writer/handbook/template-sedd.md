# Software Engineering Design Document (SEDD)

*This document is the formal proposal and technical blueprint for a new service, major feature, or significant system enhancement. It aligns Product, Engineering, and Architecture stakeholders on scope, design, and impact.*

**SEDD-ID:** *[Example: 20251031-migrate-to-dynamodb]*

**Status:** Draft — Pending Design Review

**Domain:** *[Example: Rokt Brain: Audiences & Bidding]*

**Last updated:** *[Date]*

**DRI:** *[Person]*

**Contributors:** *[People]*

**Related PRD/ADR/Strategy/Roadmap:** *[Links]*

**Target Launch:** *[Date]*

# Problem Statement

## Customer / Business Need

What core problem does this design solve in terms of **Customer Value** or **Business Impact**? Reference the originating PRFAQ, PRD, or OKR.

*Example: The current Partner Configuration service requires O(n) time to load controls, violating the p95 latency budget and increasing page abandonment by X%.*

## Success Metrics

Metrics must prove the causal impact of the engineering effort.

| Metric Type | Measurement | Target |
| :--- | :--- | :--- |
| **Functional SLO** | p95 latency for Service X | Reduce from 350 ms to <=100 ms |
| **Availability SLO** | Availability for Service Y | Maintain >=99.9% |
| **Business Metric** | Referral ingestion error rate (SoR fact) | Reduce by Y% |
| **Operational Metric** | Infrastructure cost per RPS | Reduce by Z% |

# High-Level Design and Architectural Boundary

## Proposed Solution

Provide a high-level conceptual overview. Explain how the solution works and identify its main components. This section must be accessible to a Product Manager and a Staff Engineer from another team.

## Options Considered

Compare at least two viable options against latency, cost, delivery velocity, reliability, complexity, blast radius, and time-to-build. Defend the selected option.

## Architectural Boundary and Ownership

- **Boundary:** Define where the new functionality sits. Does it cross the **Rokt Brain** boundary?
- **Encapsulation:** State which internal complexity is hidden and the sole interface exposed externally. Use gRPC for data plane or JSON/REST for control plane unless an approved exception applies.
- **System of Record:** If this design affects a core business object, identify the SoR owner and its role. Example: Attribution owns conversion-window logic.

# Detailed Deep Dive

## Data Model Changes

- **New or modified schema:** Describe database and internal data-structure changes.
- **SoR impact:** For each new object, identify the service that owns its immutable facts and interpretation logic.

## API / Interface Contract

- **New endpoints or events:** Specify public interfaces such as gRPC services, REST endpoints, or Kafka topics.
- **Versioning:** Define backward-compatible versioning and migration.

## Technology Selection

- **Database:** *[Example: DynamoDB or PostgreSQL]*
- **Language / framework:** *[Example: Go or TypeScript]*
- **Justification:** If this differs from paved-road standards, give measured justification and link the approved ADR.

# Reliability, Scaling, and Operations

- **Scalability:** Explain horizontal scaling, sharding, caching, and statelessness. Design for 2x peak load.
- **Zero trust and degradation:** Explain dependency failures and graceful degradation when the primary data store is unavailable.
- **SLOs:** Define Golden Signal SLIs/SLOs for latency, traffic, errors, and saturation.
- **Monitoring and observability:** Define Datadog metrics, Observe logs, alerts, dashboards, and runbooks.

# Implementation and Launch Plan

## Phased Rollout Strategy

- **Phase 1 — Validation:** *[Example: dark launch for approved partners]*
- **Phase 2 — Canary:** *[Example: 1% production traffic with explicit success and rollback gates]*
- **Phase 3 — Full rollout:** *[Final ramp and post-launch checks]*

## Testing Requirements

- **Unit and integration tests:** Define behavior and contract coverage.
- **End-to-end tests:** Define the cross-service workflows to verify.
- **A/B or causal validation:** Define the experiment when the design claims business impact.
- **Rollback plan:** Define automated rollback thresholds and the manual rollback procedure.

# Toil Reduction / Future Refactoring

Identify recurring work to automate, deliberate technical debt, its owner, and the trigger or date for repayment.

# Open Questions / Discovery Tasks

List unresolved design questions and dependencies. Assign each to an owner when known.
