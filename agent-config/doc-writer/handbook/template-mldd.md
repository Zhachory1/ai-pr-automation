# Machine Learning Design Document (MLDD)

*This document defines design, implementation, and operational requirements for a production-ready machine learning model. It is the primary contract between the ML Modeling Team, ML Ops, and Feature/Storage Teams.*

**MLDD-ID:** *[Example: 20251031-improve-copi-ranking]*

**Status:** Draft — Pending Design Review

**Domain:** *[Example: Rokt Brain: Audiences & Bidding]*

**Last updated:** *[Date]*

**DRI:** *[Person]*

**Contributors:** *[People]*

**Related PRD/ADR/Strategy/Roadmap:** *[Links]*

**Target Launch:** *[Date]*

# Context

## Core Problem and Hypotheses

State the specific business problem, such as cold start for new advertisers, Customer Lifetime Value prediction, or ad relevancy.

- **Hypothesis:** State the ML or statistical assumption to test. Example: Feature X will be Y times more predictive of conversion than existing features.

## Success Metrics

Metrics must align with primary business drivers such as **Value Per Transaction (VPT)** and **Incrementality**, or another explicit business requirement with a measured baseline.

| Metric Type | Measurement | Target |
| :--- | :--- | :--- |
| **Business Metric (North Star)** | Incremental ROAS (iROAS) / Cost per Incremental Acquisition (CPiA) | X% lift over baseline; expected lift >=5% |
| **Relevancy Metric** | CoPI uplift, conditional on full budget use | Y% increase |
| **Offline Metric (ML)** | AUC, F1, or task-appropriate metric on held-out data | Z |

## Operational SLOs

Define applicable strict performance boundaries for the Rokt Brain critical path.

- **Service latency SLO (p95):** Model serving latency must be <=X ms. Example: <=5 ms inside Selection Service.
- **Prediction-rate SLO:** Maintain >=Y predictions per second.
- **Data-freshness SLO:** Update predictions or features within <=Z minutes of latest SoR data.
- **Golden Signals:** Define traffic, errors, and saturation alongside latency.

## Capacity, Reliability, and Blast Radius

- **Capacity:** Size training and serving for 2x expected peak load. Define throughput, resource, saturation, and cost limits.
- **Dependency failures:** Define timeouts, isolation, and graceful degradation for unavailable features, stores, or serving dependencies.
- **Blast radius:** Define the maximum affected traffic, campaigns, partners, or regions and how failures remain contained.
- **Recovery:** Define fallback, rollback, and recovery-time expectations for training and serving failures.

# Data and Feature Engineering

## Input Sources and SoR Adherence

List required input data and the System of Record responsible for it.

| Data / Model Input | SoR Service / Owner | Data Plane | Consumption Method |
| :--- | :--- | :--- | :--- |
| Customer RUID | Network Identity | Online — Selection path | gRPC call to Feature Store |
| Last Purchase Value | Transactions | Offline — Training/batch | ETL from data lake Transactions SoR |
| Campaign Context | Rokt Ads: Campaigns | Online / projected | Projected into Brain operational store |

## Model Input Strategy

- **Online model inputs:** Identify features computed synchronously during Selection/Auction. Define transformations and retrieval needed to meet latency SLOs.
- **Offline / batch model inputs:** Identify precomputed features and required update frequency or lag.
- **New model input definitions:** Define accessible schemas and transformation logic for every new input.

## Data Drift Monitoring

Define acceptable bounds for important inputs.

- **Model Input X:** Example: alert when average purchase price shifts by 10%.
- **Model Input Y:** Example: alert when referral-rate KL divergence is >=0.2.

# Model Design and Training

Include a model and training architecture diagram.

## Options Considered

Compare at least two viable model or system approaches against expected impact, latency, training and serving cost, interpretability, complexity, reliability, and delivery time. Defend the selected option.

## Model Architecture

- **Algorithm:** *[Example: GBM/LightGBM, deep neural network, collaborative filtering]*
- **Justification:** Explain technical trade-offs, including predictive value, interpretability, and training/serving cost.
- **Interpretability:** For human-in-the-loop critical decisions, define how feature importance and explanations are measured and monitored.

## Training Pipeline Requirements

- **Platform / technology:** Use the central paved-road ML platform unless an approved exception applies.
- **Training data size / duration:** *[X days and Y TB]*
- **Retraining frequency:** *[Example: daily or weekly]*
- **Model versioning:** Define an unambiguous version scheme. Example: `v1.2.3`.

## Evaluation and Validation

- **Unit and integration testing:** Define behavior and contract coverage for feature transforms, training pipelines, model artifacts, and serving interfaces.
- **Offline testing:** Define the held-out dataset, comparison baseline, metrics, and decision threshold.
- **Leakage checks:** Define temporal, target, identity, and train/serve-skew checks.
- **Simulation:** Replay historical immutable SoR facts using as-it-would-be logic.
- **Online experiment:** Define A/B or incrementality design, guardrails, power/sample-size needs, and ramp criteria.

# Serving and Deployment

Include a serving architecture diagram.

## Deployment Artifact

- **Format:** *[Example: ONNX or TensorFlow SavedModel]*
- **Required dependencies:** *[Example: Python environment and pinned library versions]*

## Serving Interface

Define and version output with contract-first design.

| Output Parameter | Type | Description |
| :--- | :--- | :--- |
| CoPI Prediction | float | Predicted Cost per Impression score for Smart Bidding |
| Confidence Score | float | Prediction uncertainty estimate |
| Model Version | string | Active model version used for prediction |

## Deployment and Rollback Strategy

- **Deployment:** *[Example: blue/green or canary with 1% traffic]*
- **Rollback:** Define automated thresholds. Example: rollback when p95 latency exceeds X ms or error rate exceeds Y% for Z minutes.
- **Failure mode:** Define graceful degradation, such as previous-model or heuristic fallback.

# Maintenance and Post-Launch

## Ownership

- **Model code and training pipeline:** *[ML Modeling Team and DRI]*
- **Deployment infrastructure and scaling:** *[ML Ops owner]*
- **Feature Store endpoint and freshness:** *[Feature/Storage owner]*

## Monitoring

ML Ops can configure alerts, but the ML Modeling Team owns definitions.

- **Live performance SLI:** *[Example: CoPI per campaign or iROAS per hour]*
- **Model health:** *[Example: alert when moving-average CoPI predictions shift by 15%]*
- **Input health:** Alert when defined model-input drift is detected.
- **Operational health:** Define Datadog metrics, Observe logs, alerts, dashboards, and runbooks.

## Documentation

Define required on-call, debugging, monitoring, alert, ownership, and storage documentation.

## Toil Reduction / Future Refactoring

Identify recurring work to automate, deliberate technical debt, its owner, and the trigger or date for repayment.

# Open Questions / Discovery Tasks

List unresolved design questions and dependencies. Assign each to an owner when known.
