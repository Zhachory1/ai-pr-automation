# Rokt Engineering Handbook

This directory contains the unified Rokt Engineering Handbook, combining Rokt-wide developer practices with cross-team approved engineering standards. Each file covers a distinct topic to make it easy for LLMs, agents, and humans to find relevant information quickly.

**Original source**: go/dev-handbook

---

## Files

| File | Summary |
|------|---------|
| [welcome.md](welcome.md) | Rokt engineering culture, Builder DNA principles, ownership model (STO/DRI), writing-first decision making, AI-first solutioning |
| [onboarding.md](onboarding.md) | First week checklist, day 1-2 setup, finding work in Jira/Cortex/GitHub, your first change end-to-end, common new-hire FAQ |
| [quick-reference.md](quick-reference.md) | Links to all essential tools and resources (Jira, Cortex, Datadog, GitHub, etc.), key GChat channels |
| [business-context.md](business-context.md) | Company OKRs (FY2027), core business metrics (APT, CoPI, VPT, YER), advertiser metrics, product descriptions, architecture boundary map |
| [template-sedd.md](template-sedd.md) | Software Engineering Design Document template for services, APIs, infrastructure, pipelines, SDKs, and UIs |
| [template-mldd.md](template-mldd.md) | Machine Learning Design Document template for data/features, model design, evaluation, serving, and operations |
| [architecture-principles.md](architecture-principles.md) | Radical encapsulation, System of Record (SoR), supporting principles (data isolation, event-driven, low latency), anti-patterns |
| [boundaries-and-engagement.md](boundaries-and-engagement.md) | Service boundary rules, no implicit sharing, architecture team engagement process and contact (#eng-architecture) |
| [standards-paved-road.md](standards-paved-road.md) | Paved road philosophy, CI/CD via DeployKit, observability baseline, security/privacy baselines, waivers, compliance, mParticle exceptions, open source policy |
| [development-lifecycle.md](development-lifecycle.md) | Plan/Build/Ship/Operate/Learn lifecycle, Shape Up process, DeployKit deployment flow, Two-Track documentation model (Track 1: Google Drive, Track 2: GitHub), definition of "done" |
| [service-ownership.md](service-ownership.md) | Owning a service end-to-end, Cortex service catalog, documentation requirements (generate from code), SoR compliance, observability requirements, quality investment (Tech Debt:Feature ratio), defensive engineering, on-call rotation, service handover |
| [production-operations.md](production-operations.md) | Production readiness checklist, monitoring/alerting setup, post-go-live health checks, troubleshooting patterns |
| [incident-response.md](incident-response.md) | Incident declaration in Datadog, severity levels (SEV-1 through SEV-5), service tiers (T0-T3), incident management process, break-glass access, shipping fixes, post-mortem process, communication obligations |
| [coding-standards.md](coding-standards.md) | Cross-team approved coding standards: readability, modularity, naming conventions, testing pyramid, defensive coding, DRY principles, OOP design patterns, file documentation requirements |
| [testing-standards.md](testing-standards.md) | Cross-team approved testing framework: Three Pillars (Fidelity, Resilience, Precision), SMURF heuristic, test pyramid, unit/integration/E2E guidance, test doubles (stubs/mocks/fakes), flakiness mitigation, hermeticity |
| [approved-tooling.md](approved-tooling.md) | Approved languages (Java, C#, JS/TS, Python, Go) with test frameworks, IDEs, agentic dev systems, MCPs, work tracking, source control |
| [glossary.md](glossary.md) | Complete glossary of Rokt-specific and engineering terms (APT, Builder DNA, Bounded Context, Bug, Component, Cortex, DeployKit, Design Doc, DRI, Experiment, Feature, Project, Regression, Service Tier, SLO, STO, System, Task, Technical Debt, Transaction Moment, Vision Document, etc.) |
| [onboarding-resources.md](onboarding-resources.md) | Team-specific onboarding guide links (go/onboard-*), Rokt Brain service architecture, core business object SoR table with SSoT table names |

---

## How to Use These Files

**For agents/LLMs**: Glob for `engineering_handbook/*.md` to discover all files. Use filenames to narrow your search — each filename describes its topic. Read the specific file that matches your question rather than loading the entire handbook.

**For humans**: Start with this README to find the right section, then follow the link.

**Cross-references**: Each file includes a "Related files" header pointing to other relevant files in the handbook.

---

## Key Standards Files

Two files in this handbook represent cross-team approved standards:

* **[coding-standards.md](coding-standards.md)**: Approved coding practices for readability, reliability, and maintainability
* **[testing-standards.md](testing-standards.md)**: Approved testing framework and best practices

These standards complement the Rokt-wide developer practices and reflect consensus across engineering teams on quality expectations.
