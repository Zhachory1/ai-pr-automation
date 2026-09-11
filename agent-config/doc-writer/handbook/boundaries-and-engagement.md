# Service Boundaries and Architecture Engagement

> **Source**: go/dev-handbook
> **Related files**: [architecture-principles.md](architecture-principles.md) (core principles), [service-ownership.md](service-ownership.md) (owning a service)

## Service Boundaries (How Teams Build Within a Product)

Every service hides its internal implementation details. If a consumer requires knowledge of a service's internals to use it correctly, the boundary has failed and must be simplified. Contracts are designed before code, reviewed, versioned, and released with explicit compatibility guarantees and deprecation timelines. Services and platforms must be designed to support both human and AI-driven workflows, with automation and observability as first-class citizens.

Systems of Record (SoR) are respected at all times—code, configuration, and operational truth live in their canonical systems (e.g., GitHub for code; BambooHR for employee data; HubSpot for CRM; One Platform for configuration).

Consumers integrate with the owner's contract or SoR rather than recreating object definitions downstream. Security and privacy are built into every boundary. We maintain a closed-network posture, support SSO/SAML/SCIM for B2B surfaces where appropriate, encrypt data at rest and in transit, and monitor through our SIEM/IDS/IPS stack.

## Boundary Rules of Engagement

We do not allow implicit sharing across boundaries. Direct database access to another team's state is prohibited; consumers use published APIs or event schemas and, where events are used, fetch current state from the owning API rather than attempting to recreate objects. For customer-facing paths, latency is treated as a budget. Teams follow integration best practices—such as asynchronous loading and warm starts—and measure TTI/RTTI where relevant to protect customer conversion.

Operational readiness is non-negotiable for any boundary that goes live. Each service maintains a runbook, SLOs/SLIs, dashboards, and on-call coverage. Production health is continuously monitored with automated anomaly detection and clear ticket workflows—supported by our network-level checks (e.g., Sentinel) and post-go-live validation cadences.

Wherever possible, compliance and operational guardrails must be automated, reducing manual gates and empowering teams to move quickly while maintaining standards.

## Architecture Engagement

The architecture team is a service team that provides guidance, recommendations, and conversations for impactful changes on our platform. Our vision is to support a scalable, functional, and documented framework for technology choices that provides both autonomy and appropriate guidance.

**When architecture engagement is valuable:**

* Any new component
* Any significant feature
* Any major technology choice
* Any deviation from a well-used, well-understood standard or pattern
* Any major expected changes to data volume or shape (both at rest and in-flight)

**When to request a meeting:**

* Whenever you want direction—from person-to-person and team-to-team, only you can determine when you'd like guidance. The architecture team is here to help no matter where you are on the problem.

**Engagement philosophy:**

* Engage early and often during planning. Don't treat the architecture process as a "review" after you're "done." Treat the path from brainstorming to decision as a process that builds consensus, knowledge, and better overall design.
* The architecture process is a virtuous circle—decisions, output, and documentation inform and educate future decisions.
* The product team is welcome to utilize the architecture team directly for determining solution viability and rough technical feasibility.

**Getting right-sized guidance:**

Not all meetings require a large group. When you submit an Architecture Meeting Request, you may:

1. Get paired with an SME to further work on the issue
2. Get paired with someone from the architecture team
3. Get asynchronous written feedback
4. Get an Architecture Meeting scheduled

**Contact:** #eng-architecture on GChat
