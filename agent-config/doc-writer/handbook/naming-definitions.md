# Terms For Our Team

**DRI**: [Zhach Volker](mailto:zhach.volker@rokt.com)  
**Last Updated**: Oct 31, 2025

This document serves as a centralized glossary and authoritative reference point for critical terminology used across the organization, particularly for engineering, product, and strategy teams.

| Feel free to add/update terms\! This will be used by AI to help differentiate meaning and definitions. |
| :---- |

The document is structured into four primary categories:

1. **General Terms:** Defines fundamental concepts related to architecture, product lifecycle, and planning (e.g., *Product*, *System*, *Component*, *Feature*, *Project*, *TDD*, *Vision Document*).  
2. **Rokt Terms:** Defines internal company-specific terminology, roles, and programs (e.g., *DRI*, *STO*, *Builder DNA*, *Lightning Rod*, *OKRs*).  
3. **Metric Terms:** Defines key performance indicators and acronyms essential for measuring business and campaign success (e.g., *CPA*, *ROAS*, *CVR*, *SLO*).  
4. **Other Terms:** Defines miscellaneous terms related to core technology and customer interaction (e.g., *API*, *Creative*, *Rokt Placement*, *Referral*).

The explicit **Purpose** of this document is to track, define, and standardize important concepts and terms to:

* **Ease Onboarding:** Provide new developers and team members with a quick, single source of truth for all critical vocabulary.  
* **Reduce Ambiguity:** Resolve confusion among existing developers, product managers, and stakeholders by establishing precise, non-overlapping definitions for architectural concepts (like System vs. Component) and business concepts (like Project vs. Feature).  
* **Streamline Communication:** Ensure consistent, clear, and efficient technical and business discussions across the organization, facilitating easier management of work items, system design, and strategic planning.

# General Terms

| Term | Definition | Primary Abstraction Level | Key Purpose |
| :---- | :---- | :---- | :---- |
| **Product** | The customer-facing, monetizable offering. A collection of features and services that solves a specific business problem for a client. | Business / Global Architecture | Defines the **value stream** and external business context. (e.g., "Rokt Placement Engine") |
| **System** | A large, distinct, independently deployable, and logically cohesive set of services. It represents a major component in the **Service-Oriented Architecture (SOA)**, often aligning with a bounded context in **Domain-Driven Design (DDD)**. | Global Architecture | Enforces **segmentation** and clear ownership (e.g., "Inference Service," "Bidding Platform"). Reduces architectural ambiguity. |
| **Component** | A single, isolated, and highly focused microservice, library, or data pipeline *within* a larger **System**. It solves a singular, tactical problem. | System Architecture / Local Design | Promotes **modular design** and reusability. Eases onboarding and testing by limiting scope. |
| **Feature** | A distinct, measurable unit of end-user or system functionality that provides business value. It is what an engineer *delivers* and a **Product Manager** *defines*. | Product / System | Defines a **deliverable** with measurable impact. Ties engineering work directly to a business outcome. |
| **Project** | A time-boxed, cross-functional, and often cross-system **effort** required to deliver one or more related **Features**. It typically involves architectural, dependency, or business changes. | Effort / Roadmap Management | Facilitates **cross-team alignment** and resource planning. Has a defined start, end, and objective (e.g., "Migration to Kubernetes," "New Feature X Launch"). |
| **Bug** | A verifiable deviation from expected or specified system behavior. | Technical / Local | Clear signal for required **corrective action** and priority (e.g., "Latency Spike on P99," "Incorrect Model Prediction"). |
| **Task** | A non-feature-related, small, self-contained piece of work, often related to maintenance, debt, or a step within a larger **Project**. | Execution / Local | Supports **micro-planning** and tracking of non-value-add, but necessary, work (e.g., "Update dependency X," "Add unit tests to Y"). |
| **Experiment** | A time-boxed, measurable test of a hypothesis to determine its impact (e.g., A/B Test, Multi-Armed Bandit). | Measurement / Science | Directly supports the **ML/Relevancy loop** and ensures data-driven decisions. |
| **Technical Debt (TD)** | The implied cost of additional rework caused by choosing an easy (limited) solution now instead of a better, longer approach. **Deferred maintenance.** | System Architecture / Execution | Quantifies the **cost of speed**. Essential for managing IPO-driven pressure and maintaining long-term agility/predictability. |
| **Service Level Objective (SLO)** | A **target value** for a service's reliability, measured over a defined period (e.g., Latency, Throughput, Error Rate). | Reliability / System Architecture | Provides an **unambiguous, data-driven measure of success** and user experience. Crucial for connecting system stability to ad platform revenue. |
| **Bounded Context (BC)** | A logical boundary within which a specific domain model is defined, consistent, and applies. A core concept in **Domain-Driven Design (DDD)**. | Global Architecture | The strategic solution to impose **architectural clarity** on your "loosely segmented" systems. Prevents coupling and model ambiguity. |
| **Regression** | A change that causes a previously working feature or system behavior to stop working correctly, *or* to perform measurably worse (e.g., increased latency, lower model accuracy). | Quality / Execution | Expands the definition of failure beyond a crash. Emphasizes the need for robust testing and **A/B/n validation** to protect live performance metrics (CTR, revenue). |
| **Vision Document** | A high-level, stable artifact defining the **long-term purpose and ideal future state** of a Product, System, or the company itself (2-5+ years out). It answers **"Why are we doing this?"** | Executive / Global Strategy | **Inspires and aligns** the entire organization. Acts as the **North Star** for all underlying strategies and features. |
| **Strategy Document** | Defines the **high-level approach, measurable goals, and major initiatives** (Projects) required to move closer to the **Vision** (typically 6-18 months). It answers **"How will we win/achieve the Vision?"** | Product / Engineering Leadership | **Prioritizes** investments and **justifies** resource allocation. Provides the necessary context for team roadmaps. |
| **Roadmap Document** | A visual, time-based plan that sequences the specific **Projects, Epics, and Features** the team commits to deliver over a specific time horizon (typically 6-12 months). It outlines *when* and *in what order* the team executes the overarching **Strategy**. | Execution/Timeline Management | **Visibility and Dependency Management.** It is the primary tool for communicating commitments and managing expectations with stakeholders. Crucial for **predictability** in an IPO-bound environment. |
| **Design Doc (DD)** | A deep-dive artifact detailing the **System architecture, Component design, data models, APIs, trade-offs, and implementation plan** for a Feature or Project. | System / Component Design | **Reduces risk** by forcing engineers to think through complexity, dependencies, and failure modes *before* coding. Becomes the **System documentation**. |
| **Post-Mortem / Incident Review** | A formal, blameless review of a significant incident (outage, degradation, or major failure) focusing on **what happened, why it happened, the impact (SLO breach), and concrete action items** to prevent recurrence. | Reliability / Process | Drives a **culture of continuous improvement**. Focuses effort on **risk reduction** and increasing system resilience, which is critical for an IPO-ready company. |

# 

# Rokt Terms

| Term |  | Definition |  |
| :---- | ----- | :---- | ----- |
| 2-Up People Leader |  | The People Leader of your People Leader, often required for key approvals. |  |
| Builder DNA |  | A mindset defining how Roktâ€™stars operateâ€”focused on ownership, collaboration, and rapid execution. |  |
| DRI (Directly Responsible Individual) |  | Individual accountable for the success or failure of a specific project. |  |
| ExCo (Executive Committee) |  | Senior leadership team responsible for strategic decisions. |  |
| FPHP (Flexible Public Holiday Program) |  | Allows Roktâ€™stars to swap designated holidays for ones meaningful to them. |  |
| GKO (Global Kick Off) |  | An annual company event to align all Roktâ€™stars on goals and strategy. |  |
| High 5 Days |  | Bonus PTO awarded to Roktâ€™stars who use most of their annual leave. |  |
| IC Specialist |  | Senior ICs with flexible office requirements, not eligible for people leadership. |  |
| Lightning Rod |  | A one-page memo used for proposing ideas and achieving cross-functional alignment. |  |
| OKRs (Objectives and Key Results) |  | A goal-setting method using qualitative objectives and measurable key results. |  |
| PRFAQ (Press Release and FAQ) |  | A document combining a mock press release and FAQs to define a new initiative's value. |  |
| PTO (Paid Time Off) |  | Paid leave. Referred to as â€œAnnual Leaveâ€ in some markets. |  |
| REX (Roktâ€™star Extender)	 |  | ICs in non-office locations that may be engaged through third parties. |  |
| RTA (Required Technology Allowance) |  | An allowance for purchasing and maintaining compliant technology equipment. |  |
| Sabbatical |  | Paid leave available to long-tenured employees for personal pursuits. |  |
| STO (Single Threaded Owner) |  | A leader responsible for an entire mission, managing cross-functional teams with a clear northstar metric.	 |  |

#  Metric Terms

| Metric / Acronym | Definition |
| :---- | :---- |
| **CPA** | **Cost Per Acquisition**. Total cost spent to acquire a new customer, divided by number of new customers acquired. Central metric for campaign performance and optimization. |
| **ROAS** | **Return on Ad Spend**. Revenue generated by an ad campaign divided by the total spend on that campaign. Shows efficiency of marketing investment. |
| **SOV** | **Share of Voice**. The percentage of placements where an advertiserâ€™s brand appears, relative to total placements audited for a competitor/platform. Used to benchmark visibility. |
| **CPC** | **Cost Per Click**. The average cost for each click on an ad. |
| **CPR** | **Cost Per Referral**. The average cost for each customer referral (positive opt-in). |
| **CVR** | **Conversion Rate**. The percent of users who reached a target conversion event after engaging with a campaign creative (conversions/referrals x 100%). |
| **CoPI** | **Conversion per Offer Impression**. Percent of users who reached a target conversion event after seeing a Rokt Ad creative (conversions/impressions x 100%). |
| **AOV** | **Average Order Value**. The average value of orders generated from conversions. |
| **CPT** | **Cost Per Transaction**. The average cost per transaction (or conversion event) generated by a campaign. |
| **EMQ** | **Event Match Quality**. A score (1â€“10) indicating how well the customer data attached to conversion events matches real customer profiles in Roktâ€™s system. Higher EMQ \= better attribution and optimization. |
| **Engagement Rate** | Percent of customers who engage with an ad creative or campaign (referrals/impressions x 100%). |
| **Impressions (I/M)** | Number of times a campaign creative was rendered in the placement. "M" denotes a thousand impressions. |
| **Referrals (R/C)** | Number of customer opt-ins or clicks on a campaign (positive engagement). |

# Other terms

| Term | Definition |
| :---- | :---- |
| **API** | Application Programming Interface. |
| **Creative** | The content that represents an advertiserâ€™s offer to a customer. |
| **Derived Data** | Data derived from customer interactions with the Rokt Platform. |
| **End Customer** | A customer of, or visitor to, the Partnerâ€™s digital property. |
| **Engagement Campaigns** | Campaigns using the Rokt Platform for surveys, offers, loyalty programs, etc. |
| **Referral** | An End Customer interaction with the Rokt Placement indicating affirmative intent (e.g., app download, sign-up). |
| **Rokt Placement** | The technology that Rokt provides to interact with End Customers and integrate data from the Partner Website to the Rokt Platform. |
| **Upsell Campaign** | Campaigns where End Customers purchase products or services via the Rokt Placement. |

# Needs To Be Defined/Triaged

Add terms here if you donâ€™t know its definition. Leads will triage the definition in the appropriate section.

| Term | Definition |
| :---- | :---- |
| **LOBs** |  |
| **SLOs, SLIs, SLAs** |  |
|  |  |

