# Software Engineering Product Requirements Document (SEPRD)

*â¬†ï¸ \[Concise name of the project, e.g., "Implement Client-Side Retry Logic in SDK v3"\]*

**SEPRD-ID**: *\[E.g. SEPRD-20251031-add-sale-seeking-to-smartbidding\]*  
**Status**: In progress  
**Domain**: Rokt Brain: Audiences & Bidding  
**Last updated**: Oct 31, 2025  
**DRI**: Person  
**Contributors**: Person Person  
**Related Strategy/Roadmap**: File  
**Target Launch**: Date

# Context

## Problem Statement (The "Why")

**What is the user or business problem we are solving?** (Focus on the *gap* in our current system or capability.)

* *Example: Our current SDK implementation immediately fails on transient network errors, leading to a 3% impression drop rate in high-latency regions, directly impacting campaign scale.*

## Context & Strategic Alignment

**How does this project align with the Rokt Blueprint, Architectural Principles, and our current OKRs?**

* \[Reference specific OKR/Goal here, e.g., Improve **Reliability** SLO to 99.99%.\]  
* \[Reference to existing architecture/products, e.g., This impacts the **Core SDK/Tag** (Innovation Edge) and interacts with the **Rokt Brain API** (Execution Core).\]

## User Persona & Use Case

**Who is the primary beneficiary and what is the core scenario?**

* *Example: **Persona:** Technical Account Manager / **Scenario:** Client integrates the SDK into their mobile checkout flow.*  
* *Example: **Persona:** Team Engineer / **Scenario:** Reducing alert fatigue from manual rollback during weekly deployments.*

# Success & Measurement

## Target Metric (Primary KPI)

**What is the single most important, measurable outcome?** (Focus on the desired behavioral change or metric movement.)

* *Example:* **Latency:** Reduce p95 latency for critical-path calls by **10%**.  
* *Example:* **Errors:** Decrease **5xx Error Rate** in the Selection Service by **0.5%**.  
* *Example:* **Toil Reduction:** Eliminate 5 hours/week of manual deployment overhead.

## Guardrail Metrics (Constraints)

**Which key reliability, performance, or cost metrics must NOT degrade?** (Enforces Defensive Engineering.)

| Metric | Baseline Value | Max Acceptable Limit |
| :---- | :---- | :---- |
| **P95 Latency** (Overall Service) | *\[Current Value (with Date & Source)\]* | *\[Target Value (e.g., must not exceed 300ms)\]* |
| **Cost/Unit** (AWS/Cloud spend) | *\[Current Value\]* | *\[Must not exceed baseline, or define max increase.\]* |
| **Availability/SLO** | *\[Current Value\]* | *\[Must maintain 99.9% SLO or better.\]* |
| **Blast Radius** (If component fails) | *\[Current assessment, e.g., Regional failure\]* | *\[Constraint, e.g., Must be isolated to the single client thread.\]* |

## Baselines & Targets

**REQUIRED: Define the status quo before development begins.** This aligns with the "Measure" phase.

| Metric | Baseline Value | Target Value |
| :---- | :---- | :---- |
| **Primary KPI** | *\[Current Value (with Date & Source)\]* | *\[Target Value / Percentage Change\]* |
| **\[Secondary Metric: e.g., Uptime/Error Rate\]** | *\[Current Value\]* | *\[Target Value\]* |
| **\[Third Metric: e.g., Developer Time Saved\]** | *\[Current Value\]* | *\[Target Value\]* |

# Scope & Requirements

## Functional Requirements (What the system must DO)

\[Specific, observable behaviors of the new system or component.\]

* *FR1:* The component MUST handle a successful response from the Upstream Service.  
* *FR2:* The component MUST log all transient network failures to DataDog with a WARN level.  
* *FR3:* The component MUST be backward-compatible with the existing deployment pipeline.

## Non-Functional Requirements (How well the system must perform/be built)

\[Requirements for quality, security, and maintainability.\]

* *NFR1:* The service MUST be configured for **horizontal scaling** (no reliance on local state).  
* *NFR2:* The code MUST achieve **95% unit test coverage**.  
* *NFR3:* The component MUST adhere to the **Single Source of Truth** principle (referencing Campaign Config from the Rokt Ads SoR only).

## Out of Scope (Constraints & Boundaries)

**What are we explicitly NOT doing?** (Prevents scope creep and focuses the team.)

* *Example: This project does not include updating the UI for the new feature.*  
* *Example: We will not migrate the underlying database schema; we are only updating the ORM layer.*

# Stakeholders & Timeline

## Key Stakeholders (Who needs to be consulted or informed?)

| Stakeholder | Role / Team | Level of Involvement (Consult/Inform/Approve) |
| :---- | :---- | :---- |
| *\[Lead Name\]* | *Staff SE, Engineering Lead (Approver)* | Approve |
| *\[Teammate\]* | *DRI, Dependent Service \[e.g., Platform Engineering\]* | Consult |
| *\[Business Partner\]* | *\[e.g., GTM / Revenue Operations\]* | Inform |

## Target Timeline

* **PRD Finalized:** Date  
* **DD Finalized:** Date  
* **Initial Production Launch (Beta/A/B):** Date  
* **Full Ramp-up Complete:** Date

# Next Steps

1. **DD Creation:** The DRI will create the Design Document and schedule a Design Review meeting.  
2. **Trade-Off Analysis:** The DD must include a detailed analysis of at least two options, comparing them against the constraints in this document.  
3. **Observability Definition:** The DD must define the SLOs, alerts, and runbooks required for the Monitoring phase, ensuring Observability as a Feature is achieved.