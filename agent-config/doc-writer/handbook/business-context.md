# Business Context: OKRs, Metrics, and Products

> **Source**: go/dev-handbook
> **Related files**: [glossary.md](glossary.md) (term definitions), [architecture-principles.md](architecture-principles.md) (architecture boundary map)

Understanding Rokt's business context helps you make better technical decisions. Every team's work ladders up to three company-level OKRs.

## Company OKRs (FY2027)

| Objective | Key Result | What It Means |
| :--- | :--- | :--- |
| **Accelerate Adoption** | Gross Revenue > $1.08B | Win T200 accounts, drive multi-product adoption |
| **Increase Relevance (APT)** | Index APT > +15% | Improve customer experience and yield efficiency |
| **Build World-Class Rokt'stars** | Employee Engagement > 75% | Retain top talent, enable mobility |

## Core Business Metrics

| Metric | Definition | Why It Matters |
| :--- | :--- | :--- |
| **APT (Activity Per Transaction)** | Ad revenue generated per partner transaction | Primary relevancy metric—how well we monetize each transaction |
| **CoPI (Customer Outcome Performance Index)** | Measures customer relevancy (how relevant the experience is) | Input to APT—better CoPI means better customer outcomes |
| **VPT (Value Per Transaction)** | Total value generated per transaction for partners | Partner-facing value metric |
| **YER (Yield Efficiency Ratio)** | APT / CoPI | Measures how efficiently relevancy converts to revenue |

## Advertiser Metrics

| Metric | Definition |
| :--- | :--- |
| **ROAS (Return on Ad Spend)** | Advertiser's revenue relative to ad spend |
| **CPA (Cost Per Acquisition)** | Cost to acquire a customer |
| **LTV:CAC** | Customer lifetime value relative to acquisition cost |
| **EMQ (Event Matching Quality)** | Closed-loop integration quality score |

## Products at a Glance

| Product | What It Does |
| :--- | :--- |
| **Rokt Brain** | AI/ML decisioning engine—selects the most relevant experience |
| **Rokt Thanks** | Post-purchase partner-facing experiences |
| **Rokt Pay+** | Payments page monetization |
| **Rokt Ads** | Advertiser-facing performance marketing platform |
| **Rokt Catalog** | Product catalog ingestion and merchandising |
| **Aftersell** | Post-purchase upsells (primarily SMB) |
| **Upcart** | Cart upsells (primarily SMB) |
| **mParticle CDP** | Customer data platform for identity and audiences |

## Product Boundaries (What Each Product Owns)

Rokt Brain is our real-time decisioning layer that selects the most relevant experience in the transaction moment. It sits neutrally between partners and advertisers and operates within our closed, privacy-safe network.

The Rokt Ecommerce Suite provides the SDKs and tooling partners use to integrate, configure, and operate the on-site experience. It emphasizes page performance and customer safety, including guidance on Time to Interactive (TTI) and Rokt TTI (RTTI), asynchronous loading, and Subresource Integrity (SRI).

Rokt Ads gives advertisers an efficient path to acquire customers across our premium ecommerce network and is documented separately in the Ads Handbook; this boundary description focuses on the interfaces it exposes to and consumes from the rest of the platform.

mParticle (CDP) unifies customer data and identity, strengthening identification and activation across channels when used alongside the Rokt platform.

Rokt Catalog ingests, enriches, and syndicates product data for merchandising and personalization and serves as the canonical surface for catalog-level capabilities referenced by our experiences.

Each product boundary publishes only its capabilities and contracts. Internal schemas, storage, and workflows remain private and may change without notice.

## Architecture Boundary Map

The following diagram shows how data flows through Rokt's architecture, from partner/client systems through our integration layer into the core platform:

```
graph TD
    subgraph DATA_INGESTION ["DATA INGESTION (async)"]
        direction TB
        PartnerSystems["PARTNER / CLIENT SYSTEMS (Shopify, Adobe, ...)"]
        IntegrationLayer["INTEGRATION LAYER (mParticle CDP, Tealium, Liveramp, etc.)"]
        PartnerSystems --> IntegrationLayer
    end

    subgraph REQUEST_RESPONSE ["REQUEST / RESPONSE (sync)"]
        direction TB
        PartnerSurfaces["PARTNER SURFACES (Cart | Checkout | Confirm | Post-Purchase | App | Email)"]
        EcommerceAPI["ECOMMERCE API (S2S / Web / Mobile)"]
        PartnerSurfaces -- "Rokt SDK / API" --> EcommerceAPI
    end

    subgraph PRODUCT_SUITES ["PRODUCT SUITES (Innovation Edge)"]
        direction LR
        RoktAds["ROKT ADS: Campaigns, Bidding Config, Creatives"]
        RoktEcom["ROKT ECOMMERCE: Thanks | Pay+ | Aftersell | Upcart | Partner Controls"]
        RoktCat["ROKT CATALOG: Product Feeds, Merchandising, Shoppable Ads"]
    end

    subgraph ROKT_BRAIN ["ROKT BRAIN (Execution Core)"]
        direction TB
        subgraph BRAIN_FLOW ["Execution Flow"]
            direction LR
            Journey["Customer Journey & Experimentation"]
            Preselect["Candidate Preselection"]
            Selection["Selection (Auction)"]
            Journey --> Preselect --> Selection
        end
        subgraph COMPONENTS ["Support Components"]
            FeaturePlatform["Feature Platform (ML Features)"]
            CustProfiles["Customer Profiles & Identity"]
            RoktCDP["Rokt CDP (Data Ingestion)"]
            TransObjects["Transaction Objects"]
            Attribution["Attribution"]
            Explainability["Explainability"]
        end
        BRAIN_FLOW --- COMPONENTS
    end

    IntegrationLayer -- "Events (Kafka)" --> PRODUCT_SUITES
    EcommerceAPI -- "Request" --> PRODUCT_SUITES
    PRODUCT_SUITES --> ROKT_BRAIN
```

**How it works:**

- **Left arm (async)**: Partner systems → Integration Layer → Kafka events → Brain (ingestion, enrichment)
- **Right arm (sync)**: Partner surfaces → Ecommerce API → Brain (real-time selection, p95 <300ms)
- **Brain** is the execution core—self-sufficient for real-time decisioning
- **Product Suites** provide config/controls that are projected into Brain ahead of time (engine vs. steering wheel)

## Key Terms

* **Transaction Moment™**: The critical moment in the customer journey where Rokt delivers relevant experiences.
* **T200**: Top 200 strategic ecommerce accounts.
* **OKRs**: Objectives and Key Results—company and team goal-setting framework.
* **Shape Up**: Planning methodology for organizing work into time-boxed "bets."
* **Lightning Rod**: One-page proposal format for aligning on new initiatives.
* **PRFAQ**: Press Release and FAQ document for larger strategic bets.
* **RoktGPT**: Internal AI assistant for Rokt platform knowledge (ask it anything about Rokt).

For a complete glossary of Rokt terms, see [glossary.md](glossary.md).
