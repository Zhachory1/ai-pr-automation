# The E2E Ownership Manifesto

**DRI**: [Zhach Volker](mailto:zhach.volker@rokt.com)  
**Last Updated**: Oct 31, 2025

This document serves as our team's core operating manual and technical contract, designed to accelerate our transition from a newly formed team to a high-impact engineering unit. Operating under the pressure of a scaling company and an upcoming IPO, our primary challenge is maturity. 

The E2E Ownership Manifesto formalizes the expectations for every engineerâ€”from Design to On-callâ€”establishing non-negotiable standards for 

* Quality  
* Documentation   
* Reliability 

By embracing this framework, we will de-risk our core product components, accelerate our individual career growth, and collectively become a force multiplier for the entire Rokt Brain organization.

ÃŸ

# I. Our Core Ethos: Force Multipliers in the Transaction Moment

Our team's mission is to build the machine that powers relevance. We are **Staff-Level Engineers in Training**â€”whether L1 or L4â€”and we operate with the mindset of end-to-end owners. Given that our systems are new and high-leverage (ad relevancy), every action we take must prioritize clarity, reliability, and leverage.

| Rokt Value | Principle in Action | Why It Matters for Us |
| :---- | :---- | :---- |
| **Own the Outcome** | **End-to-End (E2E) Ownership:** You are accountable for the Design, Implementation, Testing, Deployment, and On-Call of your work. The project isn't done until the **SLOs are green**. | We must de-risk production. Our multi-functional components are mission-critical. Failure means direct revenue impact. |
| **Smart with Humility** | **Seek Help, Write it Down:** If you are stuck for **30+ minutes**, ask a teammate or me. Then, document the solution in a living document (DD, ADR, or bug) to prevent the next person from getting stuck. | Our collective knowledge must scale faster than our technical debt. Don't waste time solving the same problem twice. |
| **Raise the Bar** | **The 80/20 Principle (Start with Leverage):** Focus our high-excellence standards on the **highest-risk areas** (e.g., real-time auction path, data integrity pipelines). Perfection is the enemy of deployment. | We have limited time and a huge backlog. We must apply high-leverage solutions where the risk is greatest. |
| **Bias for Action** | **Automate Toil:** If you have to do a repetitive task (manual testing, log analysis, configuration change) more than **three times**, automate it with a script or delegate it to a Platform team. | Your brainpower is for solving hard problems, not for manual, repetitive work (**toil**). |

# II. Engineering Excellence Pillars

The newness of our systems and team requires a fierce commitment to quality. These three pillars define our non-negotiable standards for technical delivery:

## 1\. The Quality Ratio: Investment vs. Velocity

Our velocity is currently throttled by instability. We must actively dedicate time to paying down technical debt.

* **Tech Debt:Feature Ratio:** Our sprint commitment (capacity) for technical debt (refactoring, monitoring, performance tuning) must be between **15-20%** of our total bandwidth (a 4:1 to 5:1 ratio). This is a mandatory investment.  
* **Defensive Engineering:** Assume failure. All interfaces (APIs, feature store lookups, model predictions) must include:  
  * Timeouts, fallbacks, and circuit breakers.  
  * Strict input validation (Never trust incoming inputs).  
  * Effective use of try/catch or equivalent error handling.

## 2\. Observability as a Feature

Observability is not optional post-launch; it is a core deliverable. We use it to monitor the feature and, critically, to mentor us on system behavior.

* **Test** â†’ **Monitor** â†’ **Release:** A feature is not ready for production until its **SLOs, golden signals, and custom metrics** are fully defined, deployed, and validated.  
* **The Big 4:** For every service, we must track the golden signals in DataDog:   
  * Latency: Timers on critical path, full E2E latency, model prediction times  
  * Traffic: QPS mainly, but also request failures and successful requests  
  * Errors: Stack dumps and Call lines should by default be logged  
  * Saturation: How many pending requests? Mainly affects resource usage and memory   
* **Accountability:** If a new feature causes a violation of the team's Service Level Objectives (SLOs), the project owner (DRI) is responsible for driving the incident resolution and the subsequent post-mortem.

## 3\. Documentation is Leverage

We do not hold knowledge hostage. Documentation is not just for others; it's a tool for you to clarify your own thinking and accelerate your next task.

* [**Document Hierarchy**](https://docs.google.com/document/d/1cYdl0xgRwajR8BUJK5tF5J_AdlczaB89xWpQVWED1ZA/edit?tab=t.0#heading=h.sq3fzwvo1e3)**:** We use a standardized pipeline for all non-trivial work:  
  * **PRDs (Product Requirements Documents):** Define the **problem, constraints, and success metrics** (what are we solving and why?). \[[SEPRD](https://docs.google.com/document/d/1_q3zcoZwH5yNzOgEysCG_1TGPbXG0abEbs6nl8l_UuM/edit?tab=t.0#heading=h.qs2k8bmpd3fv) and [MLPRD](https://docs.google.com/document/d/16cUUAOYWirYDNdmE2RKzUCNRaD199DuTw1mrqNNDJTY/edit?tab=t.0#heading=h.k6e683g0208p)\]  
  * **DDs (Design Documents):** Define the **solution and trade-offs** (how are we solving it?). DDs are **living documents**; they are your detailed implementation roadmap. \[[SEDD](https://docs.google.com/document/d/1sCfmkeoDdXOVWJsqfZVMwGLNiNAqs_8ALtkYlWdy0mM/edit?tab=t.0#heading=h.6l2fkek8uurf) and [MLDD](https://docs.google.com/document/d/1w5NgNt1pXKT9yLUejM93rsBSLGV8v4N7Ol49j9dzM7Y/edit?tab=t.0)\]  
  * [**ADRs**](https://docs.google.com/document/d/1G-ORoWDsNSR7JmzulzSYGfpspZOOBi3dYUpwjpqkp08/edit?tab=t.0) **(Architecture Decision Records):** Capture non-reversible, high-impact technical decisions (e.g., choice of database, feature store paradigm) for future engineers.  
* **PR/Commit Descriptions are Living Docs:** Do not just list files and short titles to the code change. Explain the *logic* of the change, linking it to the DD. Update the PR description if the implementation diverges from the original plan.  
* [**Consistency with Terminology**](https://docs.google.com/document/d/1s9lUD0rFSLmdqp8CZ3nnDVLTlZtDCcGSE_pKrSFqxeQ/edit?tab=t.0): Utilize our centralized term definitions to minimize term confusion and improve velocity.

# III. The Standard Project Lifecycle

Every project, whether a core ML model re-architecture or a large tech debt item, follows these six stages:

## 1\. Understand (The What & Why)

The goal is to eliminate ambiguity. Your outcome here is a locked **PRD**.

* **Core Deliverables:** Define the **requirements**, list the **constraints** (latency, cost, risk), and explicitly define the **Success Metrics** (Northstar/KPIs, such as **VPT** uplift, CoPI Uplift, or Target CPA Error).  
* **The Bug:** Use the associated task/bug as a running log to track small blocks or catches during development.

## 2\. Measure (The Baseline)

You must prove the impact. Impact cannot be proven without a solid baseline.

* **Status Quo:** Establish a clear **baseline** for the success metrics (the status quo). Log the measurement logic and baseline value.  
* **Proxy Check:** Review your metrics. Are you measuring the true business outcome (**Value**), or an easily measured proxy? If it's a proxy, ensure you have multiple metrics to triangulate the latent measure.  
* **No Baseline?:** If you cannot accurately measure a baseline (such as a new feature), try to define one that correlates to VPT. If all else fails, connect with your lead/manager to find the best next step.

## 3\. Brainstorm (The How)

The goal is to find the most pragmatic path forward, not the most elegant. Your outcome here is an approved **DD**.

* **DD Content:** Detail your plan. Talk about *at least* **two viable options**. Provide a clear Pro/Con analysis for each option and defend your trade-off decision.  
* [**Design Review (DR)**](https://docs.google.com/document/d/1Aneaq40f28CdDwdrpR8yT6UIJqoQfWEUX5YEv-gqzmA/edit?tab=t.0)**:** Kick off a DR meeting. Attendees are required to read the document beforehand. The Design Owner's job is not to defend, but to **collaborate** and find the best solution. Target **one review**, two maximum. If more are needed, break the project into smaller pieces.

## 4\. Implement (The Build)

The DD is your roadmap. Focus on execution and testing.

* **"Just a Passenger":** If the DD is detailed enough, you are simply the passenger executing the plan.  
* **Testing Hierarchy:** Ensure comprehensive testing: **Unit tests** (for logic), **Integration tests** (for service contracts), and **A/B/Beta testing** (for business outcome validation).

## 5\. Release (The Rollout)

Deployment is a monitored, measured event, not a checkbox.

* **Monitor Launch Progress:** Use ramp-up deploys, feature flags, and version pushes. The most critical step is to **monitor launch progress** in real-time.  
* **Impact Validation:** Ensure the predicted impact measured during experimentation is **still seen** in the ramp-up. If metrics degrade or the predicted uplift is missing, discuss with me immediately.

## 6\. Monitor (The Long Term)

This is the **E2E Ownership** phase. Your feature is live, and you own its long-term health.

* **Alerts & Health Checks:** Set up and manage the SLOs and alerts defined in the DD.  
* **Debugging/Rollback:** Ensure clear runbooks exist for debugging and, if necessary, an immediate rollback procedure is validated.  
* **Toil Reduction:** Actively look for manual steps in operation or maintenance and automate them.  
* **Learning Cycle:** Treat every incident and performance anomaly as a learning opportunity to improve your confidence and knowledge. Your lead/manager will provide tools and support to optimize each step of this cycle.

