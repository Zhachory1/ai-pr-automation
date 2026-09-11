# **Rokt Architectural Principles: Simplicity and Structure for Speed**

Our architecture exists to solve the complex problem of real-time relevancy in the transaction moment. The primary goal of these principles is to accelerate development velocity.

## **1\. Single-Threaded Ownership and the System of Record**

Single-threaded ownership operates at every level inside Rokt. Every product, service, domain object and capability must have a single, unambiguous owner.

* **Single Source of Truth:** If a service creates or is responsible for a key business object (e.g., Conversion, Impression, Referral, Campaign, PartnerConfig, AuctionResult), that service is the System of Record (SoR). It is the sole authority responsible for the object's definition, lifecycle, validation, and integrity.  The owning service must guarantee the integrity, availability and correctness of the objects it is responsible for. A challenge in maintaining a SoR is handling conflicting requirements for historical data, such as Reporting (requiring "as-it-happened" history) versus ML (requiring "as-it-would-be" or revisionist history). We resolve this by architecturally decoupling immutable facts from mutable interpretations.  
  1. **SoR for Immutable Facts (The "What Happened"):** The service capturing the raw event (e.g., the impression, the conversion) owns the immutable, append-only record of the fact.  
  2. **SoR for Interpretation Logic (The "What It Means"):** The service responsible for the business logic that derives meaning from the facts (e.g., the Attribution service defining the conversion window) owns that logic.  
  3. **Versioned Logic and Centralized Execution:** The SoR for Logic must rigorously version its rules with clear effective dates. Downstream systems (ML or Reporting) must not implement their own versions of this logic. Instead, the SoR must provide a centralized, executable artifact (e.g., a library, UDF, or service) that can be executed by data platforms against the raw facts.  
     * **Reporting ("As-it-happened"):** Pipelines apply the version of the logic that was effective at the event's timestamp.  
     * **ML ("As-it-would-be"):** Pipelines re-process historical facts using the *current* version of the logic.  
* **Forbid Object Recreation:** Downstream services must consume the object definition from the SoR. Recreating, redefining, or attempting to infer the state of objects owned by another service is strictly forbidden across all Rokt services and products. It is an anti-pattern to rely on downstream consumers to clean up or validate data produced upstream.  
* **Humans are owners, AI are assistants:** We utilize AI assistants to accelerate development \-  however \- humans retain ultimate responsibility and ownership for all aspects of the committed code. The use of AI in code generation necessitates even stronger automated testing frameworks. Comprehensive unit, integration, and end-to-end tests are mandatory gates for deployment. AI-generated code must pass the same rigorous standards as human-generated code. We actively insert Human-in-the-Loop (HITL) for Critical Decisions for critical business functions (e.g., financial decisions or configurations with a large blast radius).

## **2\. Radical Encapsulation \- Product Level and Service Level Boundaries (Complexity Stops at the Boundary)**

First Products and then Services must aggressively hide their internal complexity. A service boundary is a promise of abstraction.

* **Two Boundary Layers:** We abstract at two layers (See Appendix B for the map):  
  1. **Product Level (e.g., Rokt Brain, Rokt Ads):** Highly opinionated boundaries that encapsulate major business domains and may be exposed externally.  
  2. **Service Level (e.g., Attribution, Selection):** Encapsulated within a product boundary, designed to enable speed and independence of teams building within that product.  
* **Complexity Stays Inside:** Internal database schemas, underlying technology choices, and private workflows must never be exposed across the service or product boundary. If a user needs organizational knowledge of a serviceâ€™s internals to use it effectively, the abstraction has failed. Exporting internal complexity externalizes costs to the rest of the organization. Services should expose *capabilities* (what they can do) rather than just *data* (CRUD operations), ensuring business logic resides within the correct domain.  
* **Zero Trust and Graceful Degradation**: No service should implicitly trust another. Downstream consumers are responsible for performing all necessary integrity checks. Every service must be resilient to upstream misconfigurations, unexpected load, and downstream failures. Services cannot assume that incoming data will be well-formed or within normal volume parameters.  
* **Decoupled Lifecycles:** To enable speed of learning, the architecture must support decoupled ML lifecycles (training, evaluation, deployment). Data must be accessible, high-quality, and adhere strictly to the System of Record principle (Principle 1\) to provide speed of innovation.  
* **Contracts are the Sole Interface**: The only interface between services is the published contract. This contract is a commitment to stability, behavior, and data semantics.  
  1. **No Implicit Sharing:** Direct access to another serviceâ€™s database or internal state is strictly prohibited.  
  2. **Contract-First Design:** Contracts (APIs and Event Streams) must be explicitly defined, versioned, and designed before implementation. Services must comply and be coupled with our depreciation policy around  backward compatibility \- as a default we support backwards compatibility in all our services.  
  3. **Standardized and Governed Protocols:** Adhere to established standards: gRPC for the real-time data plane and JSON/REST for the control plane. Data streams must have strictly typed, versioned schemas defined and published by the producer.  
* **Code is the Documentation:** Documentation should be generated directly from the source of truth (code, configurations, and API definitions). This minimizes documentation drift and facilitates AI-assisted understanding of the codebase.   
* **Push vs Pull:** Consuming services should pull from APIs exposed by systems of record.  A SOR may provide notification capabilities to subscribed services but must be done in a loosely coupled manner such as pub/sub.


## **3\. Reliability, Latency and Scalability are Core to our Mission**

Success of our mission requires Rokt to power tens of billions of ecommerce transactions that can deliver in real-time a relevant experience in every transaction for every customer. Latency directly impacts relevancy and customer outcomes. 

* **Latency:** We enforce strict latency budgets (p95 response under \~300ms end-to-end). Every 100ms increase significantly impacts customer outcomes. We prohibit synchronous calls to external services on the critical path and optimize the core real-time architecture for the lowest latency.  
* **Scalability**: All components must be designed for horizontal scalability, enabling them to quickly and efficiently handle significant spikes in activity, such as those experienced during major retail events like Black Friday and Cyber Monday, without degradation in performance.  Critical real-time systems must be designed with the fewest online dependencies as possible.  
* **Reliability**: Rokt powers some of the most sensitive client environments \- the ecommerce transaction moment. To continue to be a trusted intermediary and scale our network we need top tier reliability across our core services (99.9% up-time). To achieve this our platform is designed to be fault-tolerant and resilient, ensuring continuous operation and data integrity even in the face of unexpected failures. 

## **4\. Horizontal Platform Services and Shared Standards for Speed**

To drive organization-wide speed, our architecture clearly delineates between Product Services (implementing core logic) and Platform Services (providing foundational technical leverage). Platform Services exist solely to increase the velocity and productivity of RPD teams, optimizing for developer experience (DX).

* **Mandatory "Paved Road":** To maintain organizational efficiency, Product Services must adhere to the centrally defined Platform services and standards. These include:  
  * **Standardized Deployment:** All services must use DeployKit for CI/CD.  
  * **Observability stack:** Services must integrate with DataDog (metrics/monitoring) and Observe (logging/tracing).  
  * **Security standards:** Adherence to Roktâ€™s security standards, including encryption at rest/in transit and the principle of least privilege.  
  * **Privacy compliance:** Services must implement privacy-by-design principles to maintain consumer trust and meet regulatory requirements.  
  * **Leverage Central Libraries and Choose Boring Technology:** Use centrally-managed libraries and frameworks for common concerns like database access, messaging clients, and security. Explicitly prioritize stable, well-tested, "boring" technology choices. Novel technologies should only be adopted when they demonstrably offer a 10x improvement over existing, simpler solutions. Deviating from the paved road or using novel technology requires a formal exception process and a clear justification.  
  * **Use Mandatory Common Services**: These internal capabilities are designed to be used across many Rokt products \- Authentication and Rokt Datalake.   
* **Preferred technologies:** Our strategy emphasizes cloud-native, cloud-agnostic, open-source, cost-efficient, high talent density, and broadly supported technologies. Buy vs. build decisions are based on whether the technology is core to our mission and if we can create unique value-add.

# 

# **Appendix A: Critical Business Objects \- Definitions and Ownership**

This appendix defines Rokt's critical business objects and assigns their System of Record (SoR) ownership. Adherence to these definitions and ownership boundaries is mandatory. It explicitly separates the ownership of Immutable Facts from the ownership of Interpretation Logic, as mandated by Principle 1.B. There is more information on these objects [here](https://docs.google.com/document/d/1JkT8Fbd7c_xMoubA3T_2VqDM1H4H6MenvterpAc2QYY/edit?usp=sharing). 

| Object | Definition (Summary) | SoR for Facts (What Happened) | SoR for Logic (What it Means) |
| :---- | :---- | :---- | :---- |
| **CustomerProduct DRI: [Sam Jackson](mailto:sam.jackson@rokt.com)Service Owner: [Sam Jackson](mailto:sam.jackson@rokt.com)**  | A unique identity (person) interacting with a partner or advertiser, resolved using the identity graph based on persistent and device identifiers (RUID). | Rokt Brain: Network Identity | Rokt Brain: Network Identity (Identity Resolution Logic) |
| **Transaction (T) Product DRI: [Stuart FitzRoy](mailto:stuart@rokt.com)Service Owner: [Jinli Liang](mailto:paul.liang@rokt.com)** | An outcome from a customerâ€™s journey (typically a purchase, but expanding to other high-interactivity moments). Measured by the moments Rokt *can* power a placement. | Rokt Brain: Transactions | Rokt Brain: Transactions |
| **Rokt Transaction (RT) DRI: [Stuart FitzRoy](mailto:stuart@rokt.com) Service Owner: [Jinli Liang](mailto:paul.liang@rokt.com)** | A Transaction (T) where at least one offer from the Rokt Network (3rd party) is selected for rendering. | Rokt Brain: Transactions | Rokt Brain: Transactions |
| **Cart DRI: Matt Vincent Service Owner: [Jinli Liang](mailto:paul.liang@rokt.com)** | An ecommerce basket allowing a customers to select, store, and manage items before making a purchase (or abandoning)  | Rokt Brain: Transactions | Rokt Brain: Transactions |
| **Product Product DRI: [Clay Schubiner](mailto:clay.schubiner@rokt.com) Service Owner:tbc**  | A physical or digital item that a consumer can add to a cart and purchase. Its core attributes include (title, description, images, variants, price) | Rokt Catalog | Rokt Catalog |
| **ImpressionProduct DRI: [Stuart FitzRoy](mailto:stuart@rokt.com)Service Owner: [Jinli Liang](mailto:paul.liang@rokt.com)** | The number of times a campaign creative or element was rendered. (Note: Architectural goal is to tighten this definition to require viewability and exclude Invalid Traffic (IVT)). | Rokt Brain: Transactions (Raw render event) | Rokt Brain: Transactions (Viewability/IVT logic) |
| **ReferralProduct DRI: [Scott Jackson](mailto:scott@rokt.com)Service Owner: [Jinli Liang](mailto:paul.liang@rokt.com)** | A customer positively opting into or engaging with an offer (a positive click). Represents the separation between partner and advertiser interaction and typically triggers fulfillment and billing. | Rokt Brain: Transactions (The click event) | Rokt Brain: Transactions (Fulfillment trigger logic) |
| **Conversion:  Product DRI: [Scott Jackson](mailto:scott@rokt.com)Service Owner: [Moloy Bakshi](mailto:moloy.bakshi@rokt.com)** | An outcome event deemed valuable by an advertiser (e.g., purchase, signup). Includes Click-Through (CTC) and View-Through (VTC) attribution. | Rokt Brain: RDN (Ingestion of external conversion events) | Rokt Brain: Attribution (Defines attribution windows and models) |
| **Acquisition Product DRI[Scott Jackson](mailto:scott@rokt.com) Service Owner: [Moloy Bakshi](mailto:moloy.bakshi@rokt.com)** | A conversion event where a customer is â€˜acquiredâ€™ (recorded only once per unique customer per advertiser). (Note: Potential consolidation into "Conversion" object with "First-Time" dimension). | Rokt Brain: RDN | Rokt Brain: Attribution |
| **Activity (A) Product DRI:[Stuart FitzRoy](mailto:stuart@rokt.com)Service Owner: [Moloy Bakshi](mailto:moloy.bakshi@rokt.com)** | Sum of gross activity. *Ads:* Sum of bids for winning campaigns where a referral is generated. *Upsells:* Gross client revenue earned from Rokt-powered upsells. | Rokt Brain: Transactions | Rokt Brain: Transactions |
| **Cost (C) / Spend Product DRI: [Scott Jackson](mailto:scott@rokt.com)Service Owner: [Moloy Bakshi](mailto:moloy.bakshi@rokt.com)** | Total amount of spending on a client campaign. Typically CPR/CPC based (sum of referral bid prices). Reflects Activity when summed across all campaigns. | Rokt Brain: Transactions | Rokt Brain: Transactions |
| **Revenue (R) Product DRI:[Stuart FitzRoy](mailto:stuart@rokt.com)Service Owner: [Moloy Bakshi](mailto:moloy.bakshi@rokt.com)** | Context-dependent. *Rokt Revenue (RR):* Estimated P\&L Revenue. *Partner Revenue:* Revenue earned by the partner. | Rokt Brain: Transactions (Based on Activity) | Ecommerce Suite: Finance and Accounts (Defines Scrub Rate, Rev Share logic) |
| **Value (V)Product DRI:[Stuart FitzRoy](mailto:stuart@rokt.com)Service Owner: [Moloy Bakshi](mailto:moloy.bakshi@rokt.com)** | Total value delivered to partners (Gross Profit from upsells, Advertising revenue, LTV from 1st party campaigns, and Transaction completion rate impact). | Rokt Brain: Transactions | Rokt Brain: Transactions |
| **Expected Value (eV) Product DRI: [Stuart FitzRoy](mailto:stuart@rokt.com)Service Owner: [Umair Butt](mailto:umair.butt@rokt.com)** | Partner value proportionally redistributed based on contribution to conversions (normalizing CPA). Used for experimentation. | Rokt Brain: Transactions | Rokt Brain: Transactions (w/ input from Experimentation) |

---

# 

# **Appendix B: Principles for Boundary Definition: Core Platform vs. Product Innovation**

This map defines the high-level product boundaries and the services encapsulated within them, enforcing Principle 2 (Radical Encapsulation). Defining the boundaries between a high-performance Core (Rokt Brain) and the innovation-focused Edge (Product Suites like Rokt Ads and Ecommerce) is essential for managing complexity while maintaining velocity in a complex, interdependent network.

If the boundaries are too porous, the stability of the core Brain is threatened by rapid iteration. If the boundaries are too rigid, the product layers cannot innovate quickly enough because they are bottlenecked by the Brain's necessarily more deliberate roadmap.

We define two distinct roles within the architecture, optimized for different goals:

* **The Execution Core (Rokt Brain):** The mission of the Brain is to execute the transaction moment with extreme reliability, low latency, and massive scale.1 It is the generalized engine for relevancy across the entire network. It is optimized for stability and performance; its rate of change is deliberate and rigorous.  
    
* **The Innovation Edge (Rokt Ads, Rokt Ecommerce Suite):** The mission of the Product Suites is to rapidly innovate, experiment, and develop specialized capabilities tailored to their specific domains (Advertisers or Partners). They are optimized for velocity, flexibility, and speed of learning; their rate of change is rapid and iterative.

These principles dictate where capabilities should reside and how they migrate.

## **1\. The Critical Path Mandate (The Latency Moat)**

The primary determinant of where a function resides is its relationship to the real-time transaction moment.

* **The Real-Time Boundary:** If a function is required synchronously in the critical path to select or render an experience for the customer, it **must** reside within the Rokt Brain.  
* **The Latency Budget Rule:** Any capability that jeopardizes the Brain's latency (p95 \<300ms) or availability (99.9%+) budgets belongs in the Brain, or must be architecturally decoupled if owned externally.  
* **Synchronous Dependency Mandate:** The Brain must be self-sufficient for real-time decisioning. It cannot have synchronous dependencies on services outside its boundary during the critical path.  
* **Data Projection, Not External Calls:** If the Brain requires data owned by a Product Suite (e.g., Campaign configuration), that data must be projected/replicated into the Brain's operational data stores *before* it is needed in real-time.

## **2\. Separate Strategy Definition from Real-Time Execution**

We must distinguish between the configuration and strategy of the experience (the "What" and "Why") and the execution of the experience (the "How" and "When").

* **Engine vs. Steering Wheel:** The Brain is the engine; the Product Suites are the steering wheel. Product Suites influence the Brain's behavior by providing inputs, constraints, and configurations, rather than by modifying the Brain's internal logic.  
* **Strategy and Configuration (The Edge):** Defining business rules, optimization strategies, and configurations belongs in the Product Suites.  
  * *Examples:* Campaign authoring, defining audience segments, the *logic* for Smart Bidding, partner-specific controls.  
* **Execution and Enforcement (The Core):** The real-time application of these rules during the transaction moment belongs in the Rokt Brain.  
  * *Examples:* Real-time audience matching, executing the bid in the auction, enforcing partner controls during selection.

#### **Case Study: ML Optimization (Smart Bidding, Audience Tools)**

This principle clarifies the residency for complex, ML-heavy features, prioritizing the speed of innovation by keeping the development lifecycle at the Edge, while leveraging the execution power of the Core.

* The interfaces for defining audiences, the ML models that analyze historical data to *optimize* bidding strategies (the strategy definition), and the tools to visualize success should reside in **Rokt Ads**. This allows rapid iteration on the *intelligence* of the system without impacting the real-time path.  
* The resulting optimized bid parameters and audience definitions (the artifacts/inputs) must be pushed to the **Rokt Brain**. The Brain's Selection service then uses these inputs to execute the real-time auction.

*(Note: While the ML skills required are similar across the organization, the domain focus and required iteration speed differ. Shared expertise should be managed through horizontal structures like an ML Platform or Guild, rather than forcing architectural centralization.)*

## **3\. Innovate at the Edge, Generalize at the Core**

To maximize organizational velocity, new ideas must be tested quickly in the domain where the problem is most acutely felt, before being generalized for the whole network.

* **Default to the Edge for New Features:** New, specialized, or experimental capabilities should default to the relevant Product Suite. This maximizes iteration speed, localizes the blast radius of failures, and ensures proximity to the customer feedback loop.  
* **The Core is for Common Denominators:** The Rokt Brain should only implement capabilities that are required by *multiple* Product Suites or that apply universally across the network. The Brain is a generalization engine, not an innovation sandbox.  
* **Encapsulate Domain Complexity:** Boundaries should align with business domains. The Brain owns the core mechanics of the transaction; Rokt Ads owns the advertiser experience complexity; Rokt Ecommerce owns the partner experience complexity.

## **4\. The Graduation Path (Incubate, Validate, Graduate)**

A clear path must exist for successful innovations at the Edge to be absorbed into the Core for scaling and stabilization.

* **The Lifecycle of a Feature:**  
  1. **Incubate (Edge):** Rapid iteration and experimentation within the Product Suite. (e.g., Rokt Signal initially).  
  2. **Validate (Edge):** Prove product-market fit and stabilize the requirements.  
  3. **Graduate (Core):** The feature is rebuilt, simplified, and generalized for integration into the Rokt Brain.  
* **Criteria for Graduation:** A feature is a candidate for graduation when:  
  * It is business-critical and requires the extreme scalability, latency, or reliability guarantees of the Brain.  
  * The feature needs to be generalized for use across the entire network.  
  * The requirements have stabilized, and the focus shifts from innovation to optimization and efficiency.  
* **Example (Journey Controls):** minQS and Smart Interactions were correctly incubated in the Ecommerce Suite. They are now candidates for graduation into the Brain as they become generalized mechanisms for journey optimization across the network.

---

### **Summary Table: Residency Rules**

| Functionality Type | Primary Driver | Residency | Examples |
| :---- | :---- | :---- | :---- |
| Real-time Decisioning/Execution | Latency & Stability | **Rokt Brain** | Auction mechanics, real-time feature lookup, identity resolution, impression recording. |
| Configuration & Control Plane | Domain Ownership & DX | **Product Suites** | Campaign authoring, Partner controls UI, Advertiser onboarding, Billing configuration. |
| ML Strategy & Offline Optimization | Iteration Velocity & Focus | **Product Suites** | Smart Bidding optimization models, Audience segmentation analysis, Look-a-like model training. |
| Exploratory/New Features | Iteration Velocity & Risk | **Product Suites** | New integration types (e.g., Rokt Signal initially), experimental UI/UX. |
| Mature, Scalable, Cross-Network Features | Scale & Simplification | **Rokt Brain** | Core attribution models, standardized journey controls (after graduation). |

# **Appendix C: Service Breakdown by Product \[WIP\]**

## **Product 1\. Rokt Brain**

The primary real-time service responsible for selecting the most relevant content for each customer during the transaction moment.

* **Primary Interfaces:**  
  * **Rok Ecommerce API:** Handles requests for content, receives responses, and records interactions (impressions, referrals, clicks).  
  * **Conversions API:** Integrates conversion data for optimization.  
  * **Audiences API:** Used for targeting and look-a-like purposes.  
  * **Control Plane API**:  Used for campaign revocation, creation, and other meta data functions of the Brain.    
* **Encapsulated Services:**  
  * **Network Identity:** Identifies customers (known and unknown) and bridges identity across clients. SoR for the **Customer** object (RUID).  
  * **Selection:** Determines the most relevant experience (content and layouts) to maximize VPT for a given transaction moment. Leverages ML/AI, identity, and manages targeting and experiments.  
  * **Attribution:** Attributes outcomes to interactions. SoR for **Conversion** and **Acquisition** interpretation logic.  
  * **Transactions:** Determines what was shown and engaged with. SoR for **Impression, Referral, Transaction (T, RT), Cart, Value, Revenue, Activity,** and **Cost** objects.  
  * **RDN (Rokt Data Network):** Responsible for ingesting, validating, translating, enriching, and protecting data from partners and brands. SoR for the ingestion of external facts (e.g., conversion events).  
  * **Relevancy:** The core AI and ML models and infrastructure used by Selection.  
  * **Customer (Context):** Holds audience and customer context information used for targeting and controls (distinct from Network Identity which owns the ID).

## **Product 2\. Rokt Ecommerce Suite**

The suite of products enabling partners to integrate, manage, and optimize the Rokt experience on their ecommerce properties.

* **Encapsulated Services:**  
  * **DCUI (Dynamic Creative User Interfaces):** The engine that defines the transaction moment structure (pages, placements, layouts, components) enabling the combination of 1st and 3rd party content into native experiences.  
  * **SDK:** The primary integration tool for enabling Rokt placements and conversion tracking.  
  * **Finance and Accounts:** Rokt's internal ERP system for billing, account management, and financial configuration (e.g., revenue share rules, Scrub Rate logic).  
  * **Experimentation:** Platform for configuring, running, and analyzing experiments on the Rokt ecommerce suite.  
  * **Partner Network Activation:** Tools and interfaces for partners to manage controls, access insights, and optimize performance.  
  * **Ecommerce Relevance:** Manages client-specific requirements and relevance features (e.g., Smart Interactions, minQS, cart conversion rate optimization, repeat customer treatment).  
  * **Ecommerce Health:** Monitors performance, identifies integration issues, and alerts partners to problems.

## **Product 3\. Rokt Ads**

The advertising product providing brands and advertisers access to the Rokt Network.

* **Encapsulated Services:**  
  * **Campaigns:** Manages the lifecycle of advertising efforts, including authoring workflows, creative asset management, approval processes, reporting infrastructure, and delivery configurations. SoR for the **Campaign** object.  
  * **Audiences (Targeting and Bidding):** Manages the definition of customer segments, targeting rules, and bidding strategies (manual and smart bidding optimization).  
  * **Advertiser (Tools):** Tools for advertiser onboarding, account management, and self-service interfaces.

## **Product 4\. Rokt mParticle (CDP)**

The Customer Data Platform responsible for unifying customer data and orchestrating interactions across channels.

* **Primary Interfaces**  
  * **Platform API (Control Plane):** Used to control most aspects of the platform including data forwarding, filtering, security, and data warehouse pipes.  
  * **IDSync API:**  Used to request a MPID with known identifiers  
  * **Event Ingest:**  Accepts event batches consisting of user interaction events, user and device identifiers, user attributes, and technographic information.    
  * **Profile API:** Synchronous, low latency API to retrieve full user profile.  This api is used by customers to do real time personalization and troubleshooting.  
* **Encapsulated Services:**  
  * **Core CDP:** The foundational data infrastructure responsible for data ingestion, schema management, data quality checks, governance, and real-time data processing.  
  * **Integrations:** The extensive library of pre-built connectors (Event, Audience, Feed) for ingesting data from various sources and syndicating data to downstream marketing and analytics tools.  
  * **Identity:** The mParticle identity resolution service (IDSync), responsible for issuing persistent identifier (MPID) which can then be used by other services to unify customer data across devices and channels into a single customer profile (distinct from Rokt Brain's Network Identity).  
  * **Audiences:** Tools for segmenting customer profiles, building complex audience criteria, and activating these audiences across various channels.  
  * **Data Forwarding:**  A set of services that reliably transmit data to integration partners.    
  * **Composability:** Capabilities allowing flexible, low-code orchestration of data flows, personalized experiences, and next-best-action recommendations.

## **Product 5\. Rokt Catalog**

The platform for managing and optimizing product data for merchandising and personalization.

* **Encapsulated Services:**  
  * **Rokt Catalog (Core):** The central repository and management system for product data, including ingestion, enrichment, taxonomy management, and syndication.  
  * **Rokt Catalog for Brands:** Tools enabling brands to manage their product information and syndication within the Rokt network and partner sites.  
  * **MerchandisingAI:** AI/ML services that analyze customer behavior and catalog data to optimize product discovery, recommendations, and search relevance.

---

# 

# **Appendix C: Architectural Principles of Leading Tech Companies**

Reviewing the principles of leading technology organizations provides valuable context. It is often the *cultural and structural mandates*, not just technical best practices, that define their success.

## **Google: Scale, Sustainability, and SRE**

Googleâ€™s principles emphasize "Software Engineering over Programming," focusing on the long-term sustainability of systems rather than the initial act of writing code.

* **Sustainability Over Time:** A core concept is that engineering is "programming integrated over time." Systems must be designed to withstand constant change and be sustainable long after the original authors have left.  
* **Cognitive Scalability:** Architecture and tooling must scale organizationally, not just technically. The goal is to minimize the cognitive load required for an engineer to understand and safely modify the system (enabled by standardized tooling and the monorepo structure).  
* **Site Reliability Engineering (SRE) Culture:** Operational stability is a core engineering function. This includes embracing risk, defining Service Level Objectives (SLOs), using error budgets to balance reliability with velocity, and relentlessly eliminating "toil" (mundane, repetitive operational work).

## **Amazon (AWS): Autonomy, Ownership, and Frugality**

Amazonâ€™s engineering culture is deeply rooted in structural mandates that enforce decentralization and extreme ownership.

* **The "Bezos Mandate" (Service Orientation):** Famously, Jeff Bezos mandated that all teams must expose their data and functionality through service interfaces, and there must be no other form of inter-team communication allowed. This enforced a service-oriented architecture.  
* **Ownership (You Build It, You Run It):** Small, autonomous teams ("Two-Pizza Teams") are fully responsible for the lifecycle of their software, from design through operations.  
* **Design for Failure:** "Everything fails, all the time." Systems must be resilient and capable of withstanding the failure of individual components without impacting the customer.  
* **The Frugal Architect:** Cost must be a primary, non-functional requirement. Architects must align costs with business value and optimize continuously.

## **Netflix: Freedom, Responsibility, and Resilience**

Netflix is renowned for pioneering microservices architecture and a culture that balances autonomy with alignment.

* **Freedom and Responsibility:** Teams are given clear objectives (Context, Not Control) and the autonomy to decide how best to achieve them. They are expected to own their decisions and the outcomes.  
* **Pioneers of Resilience (Chaos Engineering):** Netflix proactively injects failures into their production systems (e.g., Chaos Monkey) to identify weaknesses before they become outages.  
* **The "Paved Road" (Aligned Autonomy):** Centralized platform teams build and support a standardized stack. While teams are free to deviate, the "paved road" is the path of least resistance and is fully supported.

## **x.AI / Elon Musk Ventures (SpaceX, Tesla)**

While x.AI is nascent, the engineering philosophy driven by Elon Musk across his companies (often referred to as "The Algorithm" or the "5-Step Process") emphasizes velocity, first principles, and aggressive simplification.

* **First Principles Thinking:** Challenge conventional thinking and break down problems to their fundamental truths rather than reasoning by analogy (copying existing solutions).  
* **The 5-Step Engineering Process:** A highly prescriptive workflow for design and optimization:  
  1. **Make Requirements Less Dumb:** Aggressively question all requirements and constraints.  
  2. **Delete the Part or Process:** Strive to remove elements. Musk has stated: "The most common error of a smart engineer is to optimize a thing that should not exist."  
  3. **Simplify and Optimize:** Optimization should only occur after deletion.  
  4. **Accelerate Cycle Time:** Move faster, but only after the first three steps.  
  5. **Automate.** (Automation comes last; automating a flawed process accelerates waste).

# **Appendix C: FAQ**

Q:  What is the priority of cost efficiency?