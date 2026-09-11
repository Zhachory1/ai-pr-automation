# Service Ownership and Engineering Excellence

> **Source**: go/dev-handbook
> **Related files**: [production-operations.md](production-operations.md) (production readiness), [incident-response.md](incident-response.md) (incidents and on-call), [architecture-principles.md](architecture-principles.md) (SoR principles)

We earn speed by making ownership unambiguous and reliability non-negotiable. This section explains what it means to own a service at Rokt, organized chronologically from creation through operation and eventual handover.

**Why**. Reliability and clear data boundaries are foundational to partner trust.

**Who**. We make accountability explicit. Single-Threaded Owners (STOs) hold mission outcomes, and every initiative and change names a Directly Responsible Individual (DRI) who owns the result. This reflects our Builder DNA: alignment with autonomy, crisp written decisions, and leaders who stay close to the work.

**What**. "Tight" operating standards apply everywhere: SLO/SLI, runbooks, on-call rotations, dashboards, and secure-by-default practices. These are paired with continuous monitoring and a documented security SDLC.

## What It Means to Own a Service

At Rokt, engineers own what they ship end to end, including on-call. Ownership is continuous from creation to retirement—taking responsibility for quality, reliability, and security from shaping work to operating in production. For on-call expectations, see the On-Call section below.

## Service Catalog and Cortex

Every service is registered in Cortex, our service catalog (go/cortex) with current metadata, ownership information, and links to key resources. Keeping these entries current is part of "definition of done" for any change that affects a service boundary or ownership.

New builders are supported through onboarding guides, a "buddy" system, and clear expectations for making their first change within the first week. Key documents, systems, and contacts are provided through the service catalog.

## Documentation Requirements

**Code is the documentation.** The best documentation is generated directly from the source of truth—code, configurations, and API definitions. This eliminates documentation drift and enables both humans and AI agents to reliably understand your services.

**Generate, don't write:**

| What to document | Source of truth | How to generate |
| :--- | :--- | :--- |
| API contracts | Code annotations | OpenAPI/Swagger schemas generated at runtime |
| Configuration options | Typed config schemas | Self-documenting config with validation |
| Data models | Schema definitions | Generated docs from actual data definitions |
| Service contracts | Interface code | Published schemas derived from implementation |

When documentation can be generated from code, it must be. Manually maintained docs that duplicate what code already expresses will drift and mislead.

**What still requires written documentation:**

* **Runbooks**: Operational guides for triage, mitigation, and recovery—linked from repo and tickets
* **ADRs**: Architecture Decision Records that capture the "why" behind interface and architectural choices
* **READMEs**: Service purpose, dependencies, and getting started instructions

These written artifacts capture intent, context, and operational knowledge that code alone cannot express. Keep them discoverable (in-repo, linked from Cortex) and current as part of your definition of done.

## System of Record (SoR) Compliance

Every service must respect Systems of Record. See [architecture-principles.md](architecture-principles.md) for the full SoR principle, the table of key SoR locations, and anti-patterns to avoid.

**Operational requirements:**

* **Consume, don't recreate**: Fetch object definitions from the owning SoR—never re-implement logic locally
* **Facts vs. interpretation**: Where a domain has "what happened" (facts) and "what it means" (interpretation), the SoR executes interpretation logic centrally so all consumers get consistent results
* **Identity controls**: Identity is handled through first-party and network identity frameworks with client-controlled governance

## Observability Requirements

Every service that reaches production must demonstrate:

* **SLO/SLIs**: Defined, reviewed with on-call, and backed by actionable alerts. Service Level Objectives define what "good" looks like, and Service Level Indicators measure whether you're meeting that bar.
* **Dashboards**: Golden signals visible to the owning team (latency, errors, saturation, traffic); logs and metrics routed to our standard observability stack/SIEM with access controls.
* **Alerts**: Actionable, tuned to reduce noise. Every alert should have a clear remediation path documented in the runbook.
* **Logging and monitoring**: Security logging and alerting expectations are followed. We log what is necessary to investigate and resolve incidents quickly (authenticated access attempts, configuration changes, cloud/audit events, and service-level signals).

Automated testing and quality gates are mandatory. Every service must demonstrate robust test coverage (unit, integration, and system tests as appropriate), and all code must pass automated checks before production deployment. Builders are expected to treat testing as a core part of their craft, not an afterthought.

## Quality Investment

We actively dedicate capacity to paying down technical debt and investing in system quality.

**Tech Debt:Feature Ratio**: Sprint commitment for technical debt (refactoring, monitoring, performance tuning, automation) should be between 10-20% of total bandwidth. This is a necessary investment to maintain velocity and prevent instability from accumulating.

**The 80/20 Leverage Principle**: Focus high-excellence standards on the highest-risk areas (real-time critical paths, data integrity pipelines, security boundaries). Apply pragmatic judgment elsewhere—perfection is the enemy of deployment.

## Defensive Engineering

Assume failure at every boundary. All interfaces (APIs, feature store lookups, model predictions, external services) must include:

* **Timeouts, fallbacks, and circuit breakers**: Prevent cascading failures
* **Strict input validation**: Never trust incoming inputs from any source
* **Effective error handling**: Use try/catch or equivalent; log errors with context; fail gracefully

## Seek Help, Write it Down

If you are stuck on a problem for more than 30 minutes, ask a teammate or lead. Once resolved, document the solution in a living document (Design Doc, ADR, or runbook) to prevent the next person from encountering the same issue. Our collective knowledge must scale faster than our technical debt.

## Automate Toil

If you perform a repetitive task (manual testing, log analysis, configuration changes, deployments) more than three times, automate it with a script, tool, or workflow. Your cognitive capacity is for solving hard problems, not for repetitive manual work. Escalate automation opportunities to platform teams where appropriate.

## On-Call and Rotation

Every service maintains a clear on-call rotation, paging policy, and escalation paths. Teams are expected to fully cover their on-call rotation.

**Individual on-call expectations**:

* Carry a laptop
* Have a phone accessible and within reception
* Be able to respond to a page and begin analysis within 15 minutes of alert

Builders on-call are expected to be available at all times during their rotation, or to otherwise have made arrangements for coverage by their teammates. It is the responsibility of the person on-call to ensure this is done.

When a production issue arises, the on-call builder is paged via Datadog alerting. The incident management process is then followed: triage using observability dashboards, consult runbooks, and prepare a fix. Incident management should leverage automation and AI tooling for rapid detection, triage, and learning. Consistency in process is enforced by platform tools, not just by policy.

## Lifecycle and Handover

Ownership is continuous from creation to retirement. Before any transfer of a system or capability, the sender and receiver confirm—at minimum—SLOs, dashboards, runbook completeness, security posture, and cost/operational expectations. This avoids "orphaned" services and ensures uninterrupted on-call coverage.

By holding a high bar on ownership and reliability—and by keeping our truths in their systems of record—we protect customer trust and keep teams moving fast without breaking the contract we have with partners and advertisers.
