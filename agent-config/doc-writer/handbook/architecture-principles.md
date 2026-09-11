# Architecture Principles

> **Source**: go/dev-handbook
> **Related files**: [boundaries-and-engagement.md](boundaries-and-engagement.md) (service boundaries and architecture team), [onboarding-resources.md](onboarding-resources.md) (Brain service architecture)

This section explains how we partition the Rokt platform so teams can move quickly and safely. We draw firm lines where uniformity creates speed and trust, and we use principles where flexibility helps us learn faster. Our boundaries are designed to protect our role as a trusted intermediary, where partners retain control of their data inside segregated accounts and Rokt processes that data only under instruction within a closed network.

Radical encapsulation occurs at two layers:

* **Product** (e.g., Brain, Ecommerce Suite, Ads, mParticle, Catalog) and
* **Service**; complexity stays inside; capabilities over CRUD; contract-first APIs/events; versioning/back-compat.

## Why Do We Define Boundaries?

Rokt exists to increase relevance in the transaction moment while upholding transparency and control for partners and customers. Our boundary model preserves this trust by ensuring that product areas expose capabilities through clear interfaces rather than internal details.

Ownership is explicit at two levels: Single-Threaded Owners (STOs) hold mission outcomes with clear north-star metrics, and every initiative assigns a Directly Responsible Individual (DRI) who is accountable for the concrete result.

## Core Architecture Principles

Rokt's architecture is built on a hierarchy of principles. Two are foundational—without them, the others don't work. The rest guide day-to-day technical decisions.

### Radical Encapsulation

Radical encapsulation is the single most important principle for enabling rapid, independent iteration—and the prerequisite for AI-first, agentic development. Without it, teams cannot move fast, and AI agents cannot safely manage services.

**The principle:** Every service fully hides its internal implementation. If a consumer needs knowledge of a service's internals to use it correctly, the boundary has failed and must be fixed. Complexity stays inside the owning service; only capabilities and contracts are exposed.

**Why this matters for AI-first development:**

* **Team velocity**: Properly encapsulated services deploy independently without coordinating with downstream consumers
* **AI-agent readiness**: Encapsulated services can be safely managed by AI agents because the blast radius of any change is contained within the boundary
* **Reduced coordination tax**: Tightly coupled services require synchronous releases, shared understanding of internals, and expensive cross-team planning—all of which break down when agents are in the loop

**What radical encapsulation requires:**

* **Contract-first design**: APIs and event schemas are designed, reviewed, and versioned before code is written
* **No shared databases**: Services never access another service's database directly—all access is through published contracts
* **Stable interfaces**: Contracts include explicit backward-compatibility policies and deprecation timelines
* **Implementation freedom**: Internal schemas, storage, and workflows may change without notice as long as contracts are honored

**Signs your boundary is failing:**

* Consumers need to understand your internal data model to integrate
* Changes to your internals require coordinated releases with other teams
* You have "shared" tables or schemas accessed by multiple services
* Your API exposes database IDs or internal state rather than domain concepts

### System of Record

Every object must be defined once, owned by one service, and consumed everywhere else via that owner's contract. Violating this principle is a leading cause of data inconsistency, duplicate logic, and avoidable incidents.

**The principle:** An object (customer, transaction, campaign, attribution, etc.) is defined and owned by exactly one service—its System of Record. All other services consume that object's definition from the SoR rather than recreating or inferring it locally.

**Why this matters:**

* **Single source of truth**: When the SoR changes an object's definition, all consumers automatically get the updated version
* **No drift**: Re-implementing object logic downstream leads to inconsistent reporting, attribution errors, and customer-impacting bugs
* **Faster debugging**: When something is wrong, you know exactly where to look—the SoR

**Operational SoRs:**

For details on the systems of record principals, their usage, and what tables they exist in, see [onboarding-resources.md](onboarding-resources.md) (Rokt Brain Service Architecture).

| Object | System of Record | Notes |
| :--- | :--- | :--- |
| Campaign & UX config | One Platform (Ads / Thanks) | Advertiser and partner product configuration |
| Service metadata & ownership | Cortex | Service catalog — use go/cortex to find owners |
| Code & infrastructure | GitHub | All code, config, and IaC |

**Anti-patterns to avoid:**

* Re-deriving whether a conversion occurred instead of consuming from the Attribution service
* Building local impression or transaction counts from raw event streams instead of the SSoT tables
* Inferring customer identity rather than resolving it through the Identity service
* Building reports from bronze-layer data that bypasses the SoR's interpretation logic

**Finding the owner:** Use Cortex (go/cortex) to look up which service owns a given object or domain. Browse SSoT tables and lineage in DataHub. If ownership is unclear, raise it in #eng-architecture.

### Supporting Principles

These principles work because radical encapsulation and System of Record are in place:

**Data Isolation & One-Way Data Flow:** Client-provided first-party data is used **only** to benefit that client's outcomes and is never shared with others. The Rokt Brain operates as a one-way system—client data flows in for optimization, but no sensitive data ever leaks out.

**Event-Driven Integration:** Use asynchronous messaging (Kafka events) for integrating systems. Publishing events decouples the sender from the receiver and avoids blocking calls. Design consumers to handle events eventually, updating caches or triggers as needed.

**Decoupled Microservices & Team Autonomy:** Each network service exposes stable APIs or event interfaces, minimizing direct inter-service calls. We align service boundaries with team boundaries following Conway's Law.

**Ultra-Low Latency at Massive Scale:** Critical decisioning is done in-memory or via pre-computed models. We enforce strict latency budgets (p95 under ~300ms for consumer-facing requests) and avoid synchronous calls to external services in the critical hot path. Cold paths exist for non-latency-sensitive computations to preserve capacity in the real-time hot path services.

**Simplicity and Sustainable Growth:** Minimize real-time API interdependencies and "chatty" communication patterns. Prefer simple, loosely coupled integrations over intricate real-time orchestrations.

**Key Anti-Patterns to Avoid:**

* Synchronous cross-boundary calls during user transactions
* Chatty microservices requiring sequential calls between services
* Tight integration coupling (core logic aware of specific data sources)
* Commingling client data or identities
* Over-engineering real-time solutions when batch processing suffices
