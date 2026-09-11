# ML Product Requirements Document (MLPRD) Template

*â¬†ï¸ \[Concise name of the project, e.g., "Add Sale Seeking Feature into Smart Bidding Model"\]*

**MLPRD-ID**: *\[E.g. MLPRD-20251031-add-sale-seeking-to-smartbidding\]*  
**Status**: In progress  
**Domain**: Rokt Brain: Audiences & Bidding  
**Last updated**: Oct 31, 2025  
**DRI**: Person  
**Contributors**: Person Person  
**Related Strategy/Roadmap**: File  
**Target Launch**: Date

# Context

## Problem Statement

**What is the business problem we are solving?** (Focus on impact, not solution.)

* *Example: Advertiser campaigns are consistently bottlenecked by low scale, failing to spend allocated budget, despite demonstrating high efficiency (ROAS), indicating we are failing to identify and target a broader pool of high-propensity users.*

## Context & Strategic Alignment

**How does this project align with the Rokt Blueprint, the Rokt Brain Charter, and our current OKRs?**

* *\[Reference specific OKR/Goal here, e.g., Increase Network Identity match rate, Improve CoPI Uplift.\]*  
* *\[Reference to existing architecture/products, e.g., This impacts the **Selection Service** within the **Rokt Brain**.\]*

# Success & Measurement

## Target Metric (Northstar / Primary KPI)

**What is the single most important business metric we are trying to move?**

* *Select one:* **VPT Uplift**, **CoPI Uplift**, **Target CPA Error** (for bidding models), **Scale / Coverage** (e.g., % of eligible impressions served).

## Guardrail Metrics (Constraints)

**Which key metrics must NOT degrade?** (Focus on reliability and safety.)

* **Latency:** \[Define p95 latency target, e.g., 300ms p95 on the Selection Critical Path.\]  
* **Availability/SLO:** \[Define minimum service availability, e.g., 99.9% uptime.\]  
* **Cost/Inference:** \[Define max cost per model inference or Feature Store lookup.\]  
* **Data Integrity:** \[Define acceptable error rate for feature ingestion/lookups.\]

## Baselines & Targets

**REQUIRED: Define the status quo before development begins.** This validates the "Measure" phase of the lifecycle.

| Metric | Baseline Value | Target Value |
| :---- | :---- | :---- |
| **Primary KPI**  | *\[Current Value (with Date & Source)\]* | *\[Target Value / Percentage Lift\]* |
| **Scale/Coverage** | *\[Current Value\]* | *\[Target Value\]* |
| **Model Freshness** (Latency to Data) | *\[Current Value\]* | *\[Target Value\]* |
| **\[Custom Guardrail Metric\]** | *\[Current Value\]* | *\[Target Value\]* |

# Scope & Requirements

## Requirements Summary

*\[A high-level list of desired capabilities.\]*

## Out of Scope (Constraints & Boundaries)

**What are we explicitly NOT doing?** (This is crucial for preventing scope creep.)

* *Example: We will not implement real-time identity resolution; we will rely only on existing Network Identity RUIDs.*  
* *Example: This project does not involve integrating a new first-party data source.*

## ML-Specific Requirements

| Area | Requirement | Notes/Justification |
| :---- | :---- | :---- |
| **Data/Features** | *\[List any new features/data required from Feature Store or RDN\]* | *\[e.g., Requires rage\_click\_count feature from Dynamic Signals Rearchitecture.\]* |
| **Model Type** | *\[e.g., Classification, Regression, Deep Learning, Transformer\]* | *\[e.g., Requires a transition from Logistic Regression to a DNN for CoPI prediction.\]* |
| **Training** | *\[Define required model size, training frequency, required GPU/CPU allocation\]* | *\[e.g., Daily retraining required. Must fit within the existing Kubeflow pipeline.\]* |
| **Serving** | *\[Define if the model runs in the **Real-Time Critical Path** or **Offline**\]* | *\[e.g., Must serve inference in under **5ms** within the Selection Service.\]* |
| **Experimentation** | *\[Define A/B test strategy for release\]* | *\[e.g., Must be rolled out via a 10% WHS Incrementality study on the Advertisers side.\]* |

# Stakeholders & Timeline

## Key Stakeholders (Who needs to be consulted or informed?)

| Stakeholder | Role / Team | Level of Involvement (Consult/Inform/Approve) |
| :---- | :---- | :---- |
| *\[Lead Name\]* | *Staff SE, ML Engineering (Approver)* | Approve |
| *\[PM/Business Partner\]* | *Product Manager, \[Specific Product\]* | Approve / Consult |
| *\[Teammate\]* | *DRI, Dependent Service \[e.g., Feature Store\]* | Consult |
| *\[Relevant Exec/Leader\]* | *\[e.g., Head of Rokt Brain, CTO\]* | Inform |

## Target Timeline

* **PRD Finalized:** Date  
* **DD Finalized:** Date  
* **Initial Production Launch (Beta/A/B):** Date  
* **Full Ramp-up Complete:** Date

# 5\. Next Steps

1. **DD Creation:** The DRI will create the Design Document and schedule a Design Review meeting.  
2. **Trade-Off Analysis:** The DD must include a detailed analysis of at least two options, comparing them against the constraints in this doc.  
3. **Observability Definition:** The DD must define the SLOs, alerts, and runbooks required for the Monitoring phase.