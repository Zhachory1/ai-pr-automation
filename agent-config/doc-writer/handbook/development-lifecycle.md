# Development Lifecycle: Plan, Build, Ship, Operate, Learn

> **Source**: go/dev-handbook
> **Related files**: [standards-paved-road.md](standards-paved-road.md) (CI/CD standards), [service-ownership.md](service-ownership.md) (ownership expectations), [production-operations.md](production-operations.md) (production readiness), [coding-standards.md](coding-standards.md) (code quality), [testing-standards.md](testing-standards.md) (test quality)

This section describes the practical lifecycle that every builder follows at Rokt, from shaping work through operating in production. While implementation details will vary by team, every builder is expected to align with this SDLC.

## Plan: Shaping and Alignment

We align in writing (Lightning Rod/PRFAQ), identify dependencies on systems of record, and define how success will be measured before implementation starts.

Rokt follows the planning process outlined in Shape Up. Builders start by aligning on company and team OKRs, then participate in shaping work into "bets" or project increments. This often means proposing ideas, writing a Lightning Rod or launchpad, and collaborating to define the smallest valuable change. Every builder at Rokt is expected to write launchpads and place bets.

Once a bet is approved into a cycle, work items are tracked in Jira. Builders assigned to that bet are expected to break down their own tasks and plan their approach to fulfil the requirements of that bet.

### Planning Phase Details

The goal is to eliminate ambiguity and lock alignment before implementation starts.

**Core deliverables for the Plan phase:**

* **Lock a PRD (Product Requirements Document)**: Define the requirements, constraints, and success metrics (what are we solving and why?). This is the contract that defines what we are building.
* **Establish baseline measurements**: You must prove impact. Impact cannot be proven without a solid baseline. Log current performance so you can demonstrate improvement.
* **Identify dependencies**: Map dependencies on Systems of Record and other teams. Surface blockers early.

**Write a Design Doc (DD)**: Detail your plan with at least two viable options. Provide a clear trade-off analysis for each option and defend your choice. The DD includes:

* System architecture and component design
* API contracts and data models
* Baseline metrics and success criteria
* SLO definitions
* Trade-offs and alternatives considered

**Run a Design Review (DR)**: Attendees are required to read the document beforehand. The Design Owner's job is not to defend, but to collaborate and find the best solution. Target one review, two maximum. If more are needed, break the project into smaller pieces.

**Capture key decisions in ADRs (Architecture Decision Records)**: When a significant, non-reversible technical decision is made (e.g., database choice, protocol selection), write an ADR to preserve the context and rationale for future engineers.

## Build: Writing Code

We work in small, mergeable increments with reviewed code and infrastructure in GitHub. We test our code as we go, both in units and in systems, to ensure we are delivering high-quality solutions.

Developers pick up work items from their assigned bet in the current cycle. They create a branch in the relevant GitHub repo off the main branch to develop their code to satisfy that work item. There can be a many-to-many relationship between branches, commits, and Jira items.

All code is developed with unit and system tests, then submitted as a Pull Request in GitHub for peer review. Passing automated checks and teammate sign-off are required before merging to main. Quality is each builder's responsibility—all code must have meaningful, automated tests.

### Building Track 2 Documentation

The DD is your roadmap during implementation. As you build, you also build the Track 2 documentation (GitHub-based operational docs) alongside your code:

* **README.md**: Service purpose, owner, links to project folder, runbook, and SLO dashboard
* **ARCHITECTURE.md**: System diagram, dependencies, API contracts
* **ONCALL.md**: Runbooks for common alerts, debugging steps, rollback procedures
* **SLO.md**: Service Level Objectives, health check dashboard links

These documents are not an afterthought—they are deliverables. A feature is not "done" until its Track 2 docs are complete.

### Code Quality Standards

Reference [coding-standards.md](coding-standards.md) for detailed expectations on readability, modularity, naming conventions, testing requirements, and defensive coding practices.

Reference [testing-standards.md](testing-standards.md) for the complete testing framework: the Three Pillars (Fidelity, Resilience, Precision), the SMURF heuristic, test pyramid guidance, and best practices for unit, integration, and E2E tests.

## Ship: Code Review and Deployment

We release behind flags or in small batches. We document change and rollback in the PR/ADR. For customer-facing paths, we treat latency as a budget and apply integration best practices (asynchronous loading, warm starts, SRI where applicable).

Code merged to main is shipped via the paved CI/CD pipeline (DeployKit or equivalent). Some orgs may use different deploy tools (e.g., mParticle release process). It is always expected that code merged to main is ready to deploy to production, and will go live to customers without any advanced warning to the merge author.

### Deploying with DeployKit

DeployKit is Rokt's CI/CD abstraction layer that sits in front of Buildkite (or Concourse for mParticle). Builders configure deployments through DeployKit; monitor status and approve blocking steps directly in Buildkite.

**Basic flow:**

1. Merge approved PR to main branch
2. DeployKit triggers Buildkite pipeline—builds, tests, and validates
3. Artifacts deploy through staging gates (approve in Buildkite if blocking)
4. Production deployment with automated rollback capability

**Key principles:**

* All deployments are immutable—no manual server changes
* Rollback plans must be explicit before shipping
* Pipeline includes automated testing, security scanning, and quality gates

**Monitoring deployments:** Go directly to Buildkite (go/buildkite) to watch pipeline progress and approve any blocking steps.

For DeployKit configuration and troubleshooting, see the DeployKit documentation (github.com/rokt/deploy-kit).

Builders monitor deployments using Datadog, Chronosphere, or other observability tools, updating runbooks and monitoring as needed.

### Shipping as a Measured Event

Deployment is a monitored, measured event, not a checkbox.

* **Monitor launch progress**: Use ramp-up deploys, feature flags, and version pushes. Monitor in real-time.
* **Impact validation**: Ensure the predicted impact measured during experimentation is still seen in the ramp-up. If metrics degrade or the predicted uplift is missing, escalate immediately.

## Operate: Production Readiness

Production readiness is non-negotiable: every service maintains a runbook, SLO/SLIs, dashboards, and an on-call plan. See [service-ownership.md](service-ownership.md) for on-call expectations and [production-operations.md](production-operations.md) for the complete checklist.

### Operating for Long-Term Health

This is the E2E Ownership phase. Your feature is live, and you own its long-term health.

* **Alerts and health checks**: Set up and manage the SLOs and alerts defined in the DD.
* **Done when SLOs are green**: A project is not complete when code ships. It is complete when the SLOs are consistently met and the system is stable.
* **Debugging and rollback**: Ensure clear runbooks exist for debugging and immediate rollback procedures are validated.
* **Toil reduction**: Actively look for manual steps in operation or maintenance and automate them. If a task is repeated more than three times, it should be scripted or delegated to a platform team.

## Learn: Continuous Improvement

We hold concise, blame-free post-incident reviews, update runbooks and alerts, and—when behavior or definitions change—align our canonical data/object documentation so downstream consumers remain correct by construction.

The Incident Commander generates a post-mortem in Datadog Incidents and schedules an incident review meeting to capture learnings and prevent recurrence. For the complete post-incident review process, see [incident-response.md](incident-response.md).

### The Learning Cycle

Treat every incident and performance anomaly as a learning opportunity:

* **Incident reviews**: Formal, blameless reviews focusing on what happened, why it happened, the impact (SLO breach), and concrete action items to prevent recurrence.
* **Update runbooks**: After resolving a non-trivial bug or incident, add its symptoms and fix to the ONCALL.md runbook. This is how we pay down knowledge debt and support the rest of the team.
* **Refine alerts**: Tune alerts to reduce noise and improve signal. Every alert should have a clear remediation path.

## The Two-Track Documentation Model

We maintain two distinct documentation tracks, each with a clear purpose and audience:

**Track 1: Google Drive (The "Why")**

* **Purpose**: Strategy, collaboration, design, and requirements—where we decide what to build and why.
* **Audience**: Cross-functional teams (Eng, PM, GTM, Leadership)
* **Artifacts**: Strategy Docs, PRDs, Design Docs (DDs), ADRs, Roadmaps

**Track 2: GitHub (The "How")**

* **Purpose**: The "as-built" technical source of truth—the living manual for running services.
* **Audience**: Engineers (especially on-call) and AI Agents
* **Artifacts**: README, ARCHITECTURE.md, ONCALL.md, SLO.md, runbooks

Both tracks are required. Track 1 captures intent and decision-making context. Track 2 captures operational reality and enables rapid incident response.

## What "Done" Looks Like

"Done" has three phases:

1. **Before work starts**: Service is set up on DeployKit and uses paved clients/services
2. **Before shipping**: Team proves production readiness (runbook, SLOs, alerts, tests, security review, Track 2 docs complete)
3. **After shipping**: Team verifies telemetry, completes health checks, tunes alerts, and confirms SLOs are green

For the complete production readiness checklist, see [production-operations.md](production-operations.md).

Passing automation is a requirement for merging code. We do not 'throw it over the wall' or expect others to catch our mistakes.
