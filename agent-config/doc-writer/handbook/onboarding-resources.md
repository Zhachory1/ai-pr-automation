# Team Onboarding Resources and Brain Architecture

> **Source**: go/dev-handbook
> **Related files**: [onboarding.md](onboarding.md) (your first week), [architecture-principles.md](architecture-principles.md) (SoR principles)

## Team Onboarding Guides

Many teams maintain their own onboarding guides. Check with your team lead or find your team's Confluence space:

* **Formats & Experience**: go/onboard-formats
* **Campaign**: go/onboard-campaign
* **Fulfilment**: go/onboard-fulfilment
* **Optimization**: go/onboard-optimization
* **Rokt Ads**: go/onboard-ads
* **Auth**: go/onboard-auth
* **Reporting**: go/onboard-reporting
* **Realtime Relevance**: go/onboard-realtime
* **Selector**: go/onboard-selector
* **TestOps**: go/onboard-testops
* **SRE/Platform**: go/onboard-sre

For general engineering onboarding, see go/eng101. For architecture overview, see the Rokt Platform Application Architecture 101 and Engineering teams and owners documentation.

## Rokt Brain Service Architecture

This section provides the detailed service architecture for the Rokt Brain and core platform services. For the complete service architecture document, see Rokt Brain Service Architecture V3.

The architecture document includes:

* Service inventory: All core services, their responsibilities, and ownership
* System of Record mappings: Which service owns which critical objects
* Integration patterns: How services communicate (events, APIs, contracts)
* Data flow diagrams: How data moves through the platform

### Core Business Object Systems of Record

| Object | System of Record | SSoT / Notes |
| :--- | :--- | :--- |
| Transaction (T, RT, P) | Transaction Objects (Data Platform) | `lake_ssot.transaction` — did a transaction occur on the partner's confirmation page? |
| Impression (Offer, Placement) | Transaction Objects (Data Platform) | `lake_ssot.offerimpression` — did we show an offer to a customer? |
| Referral, Interaction & Activity | Transaction Objects (Data Platform) | Customer engagement events (clicks, declines) |
| Revenue & Cost | Transaction Objects (Data Platform) | `lake_ssot.revenue` — Gross, Rokt, and Partner revenue |
| Conversion & Acquisition | Attribution | Did a prior marketing interaction cause a conversion? Versioned attribution windows |
| Customer Identity (RUID/RID) | Customer Profiles & Identity | Who is this customer? Cross-network identity resolution |
| Customer Facts (1P/3P data) | mParticle / Rokt CDP | Ingested and normalized external customer data |
| Experiment Assignments | Customer Journey & Experimentation | Cohort allocation, holdouts, experiment flags |

All SSoT tables follow Rokt's medallion architecture (Bronze → Silver → Gold) and are discoverable in DataHub.
