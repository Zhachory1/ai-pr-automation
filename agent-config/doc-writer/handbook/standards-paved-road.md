# Standards and The Paved Road

> **Source**: go/dev-handbook
> **Related files**: [approved-tooling.md](approved-tooling.md) (languages, IDEs, MCPs), [development-lifecycle.md](development-lifecycle.md) (CI/CD in practice)

At Rokt, every developer's day is shaped by our "paved road"—a set of approved tools, processes, and engineering standards designed to make building fast, secure, and consistent across teams. While the paved road defines the non-negotiables, teams have autonomy to choose their own implementation details and experiment within this framework—provided that learnings and improvements are documented and shared back into this handbook.

**Why**. Standardization compounds speed and protects partner trust. Our clients retain control of their data in segregated accounts; Rokt processes under instruction inside a closed network. The paved road embeds privacy-by-design and zero-trust defaults so every team benefits from the same guardrails.

**Who**. Platform Engineering maintains the paved road; Security owns the baseline controls; Product/Domain teams adopt by default. STOs/DRIs are accountable for using the paved road (or documenting waivers) on their initiatives. Security Champions in each squad help apply the standards day-to-day.

**How**. "Adopt by default." Deviations require an ADR with a time-boxed waiver, an identified owner, risk/rollback, and a review date. Waivers expire unless renewed with fresh rationale. New learnings from these deviations must roll forward into the Paved Road to improve life for everyone at Rokt.

**What**. A small set of mandatory standards: CI/CD via DeployKit; a shared observability stack; security/privacy baselines; central clients and common services; and a "choose boring tech" principle that favors proven components unless a 10x case is documented.

## What the Paved Road Is

The paved road is the baseline that lets you ship safely without having to re-invent foundations. When you join a team or start a new service, these choices are already made. The approved list of agentic and AI-enabled tools is reviewed regularly. Builders are encouraged to propose new tools and share usage learnings so that the paved road evolves with the needs of the organization.

## Approved Tooling and Languages

We favor stable, well-supported, cloud-native, cost-aware components. Developers may use any IDEs they prefer from the supported list. AI agents are strongly encouraged as co-authors, but ownership and responsibility ultimately remain with the builder.

Novel technology is introduced only when there is a documented 10x advantage over the paved choice, proven in a time-boxed trial with measured outcomes and captured in an ADR.

For the complete list of approved languages, testing frameworks, IDEs, agentic development systems, and Model Context Protocols (MCPs), see [approved-tooling.md](approved-tooling.md).

## CI/CD and Change Management

All Rokt services ship through DeployKit using peer review, automated checks, artifact provenance, and auditable rollbacks; manual changes on servers are not permitted. Infrastructure is expressed as code and deployed immutably, so the only way to change production is through the pipeline. Code merged to main is ready to deploy to production, and will go live to customers without any advanced warning to the merge author.

Pipelines must include automated testing, quality gates, and AI-assisted code review and analysis. Security is a required part of the pipeline: teams apply threat modeling as needed and run static analysis and composition checks, with post-deployment monitoring to catch regressions early.

Infrastructure always lives as code. We do not make production changes directly into a cloud UI as a routine part of development.

## Observability Baseline

Every service emits metrics, logs, and traces to the shared stack (Datadog for metrics/monitoring; Observe for logging/tracing; Chronosphere for additional monitoring) and owns golden dashboards and actionable alerts before go-live. We log what is necessary to investigate and resolve incidents quickly (for example, authenticated access attempts, configuration changes, cloud/audit events, and service-level signals) and retain the evidence required by our logging and monitoring policy.

Builders are responsible for ensuring that their code includes the needed observability to monitor for health as customers interact with their services.

## Security and Privacy Baselines

Our security controls enforce the closed-network posture described in [architecture-principles.md](architecture-principles.md). Key controls:

* **Access**: SSO/SAML/SCIM, MFA for privileged access, least privilege by default
* **Data protection**: Encryption in transit and at rest, approved cloud-only backups, no commingling of client data
* **Training**: Continuous security awareness, secure-coding training, Security Champions program

## Central Clients and Common Services

Teams use centrally maintained clients and frameworks for standard concerns (authentication, messaging, storage, telemetry) and prefer common platform services over bespoke equivalents. If a paved client or service lacks a needed capability, extend the shared component rather than fork a parallel stack.

Systems of Record (SoR) are respected at all times—we integrate with the owner's contract rather than recreating object definitions downstream. For detailed SoR rules, see [architecture-principles.md](architecture-principles.md).

## Waivers and Exceptions

When diverging is the fastest safe path, teams raise an ADR describing the rationale, risks, mitigations, owner, expiry date, and rollback/exit criteria. Waivers are reviewed on a regular cadence and expire unless renewed with fresh evidence; expired waivers automatically revert the service to the paved choice.

## Compliance and Customer Trust

We keep evidence easy to produce—pipeline logs, approvals, test artifacts, and operational runbooks—because external commitments such as ISO 27001, SOC 2, and client DPAs depend on consistent controls and auditability. The paved road is how we meet those obligations without ad-hoc work, including timely incident notification, return/deletion of data on request, and reasonable audit support under our agreements.

## Legacy Systems and Migration

Some legacy parts of Rokt are using older deployment or monitoring methodologies and are in the process of migrating to the paved road. As a living document, this handbook acknowledges the current state while setting the direction for all net-new work.

**mParticle exceptions**:

* **Deployment**: mParticle services currently use Concourse for CI/CD (not DeployKit)
* **Observability**: mParticle services currently use Chronosphere and Observe for monitoring (not Datadog)
* **Migration**: These services are expected to migrate to the paved road over time

**Standard for net-new work**: All new services and products must use the paved road (DeployKit, Datadog, Observe). Builders working on existing legacy systems should consult with their team leads on migration timelines and any interim patterns to follow.

## Open Source Philosophy

Rokt takes a principled approach to open source software:

**Consumption**: We prefer well-maintained open source components that align with our tech radar. Dependencies are tracked and scanned for vulnerabilities as part of our security baseline.

**Contribution**: Builders are encouraged to contribute back to projects we depend on. For contributions on company time, follow the Open Source Policy guidelines.

**Publishing**: Publishing Rokt-owned code as open source requires approval. See the Open Source Policy for the evaluation criteria and approval process.
