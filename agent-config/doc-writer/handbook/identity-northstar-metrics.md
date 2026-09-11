# Identity Northstar Metrics (& Measurement of the Metric)

Bruce Buchanan | July 23rd, 2025

[Grok query](https://grok.com/share/bGVnYWN5_6721a276-df1b-4a22-9627-5e15d63515b7) and [Gemini query](https://docs.google.com/document/d/1_OEAOVwrEoealpHITrwZCvDlHIsj8KDGrkgHSln_RFc/edit?usp=sharing)

### **Research and Ideas for Northstar Metrics in Identity Resolution for the Rokt Identity Team**

**Northstar Metrics Overview**: A northstar metric (NSM) is a single, guiding KPI that captures the core value delivered by a team or product, while aligning with broader business goals (e.g., growth and customer relevancy). Sources like Amplitude and LoginRadius emphasize that an effective NSM should be customer-value-focused, strategy-aligned, and a leading indicator of revenue. For identity teams, this often involves balancing accuracy (correct bridges) with risk (incorrect bridges), to enable autonomy without silos.

**Challenges Alignment**: Research from sources like Lotame, LiveRamp, and Roqad highlights similar issues in identity resolution: slow iteration due to stakeholder dependencies, incentivizing "false positives" (e.g., bad bridges for short-term gains), and stagnation from perfectionism. Best practices recommend metrics that promote iterative progress, such as trade-off scores (e.g., F1 Score) and probabilistic thresholds.

---

### **Range of Approaches and Metrics for Northstar**

Your proposed trade-off (maximize correct bridges, minimize incorrect ones) aligns well with entity resolution evaluation frameworks from sources like SpringerOpen and Roqad, which use supervised/unsupervised metrics to measure accuracy. An ideal NSM should be:

- **Autonomy-Enabling**: Measurable internally by the team (e.g., via simulations or samples) for fast cycles (\<2-4 weeks).

- **Business-Aligned**: Linked to outcomes like improved customer relevancy (e.g., personalized offers) and growth (e.g., conversion lift from unified profiles).

- **Iterable**: Focus on improvements over perfection, with thresholds to avoid bad behaviors.

Here are **5 suggested approaches**, each with 2-3 specific metrics. These draw from research on identity resolution accuracy (e.g., precision/recall) and northstar examples (e.g., from Amplitude for identity-focused products). You could start with one as your NSM and use others as supporting KPIs.

1. **Accuracy Trade-Off Approach** (Core to Your Idea: Balances Correct vs. Incorrect Bridges)

   - Focus: Use ML-based scoring to quantify bridge quality. This promotes quick testing of bridge rules without full stakeholder buy-in.

   - Metrics:

     - **F1 Score for Bridge Accuracy**: Harmonic mean of Precision (ratio of correct bridges to total bridges created) and Recall (ratio of correct bridges to all possible true bridges). Target: \>0.85, per Roqad's benchmarks for identity graphs. This directly addresses fake conversions by penalizing low precision.

     - **Error Rate Ratio**: (Incorrect Bridges / Total Bridges) vs. (Correct Bridges / Total Possible). Aim for \<5% error while \>70% correct, adjustable via A/B tests.

     - NSM Fit: High; it's self-measurable via sampled validation and aligns with relevancy (better accuracy â†’ better personalization).

2. **Profile Completeness Approach** (Emphasizes Business Value from Unified Identities)

   - Focus: Measure how well bridges create "golden" unified profiles that drive downstream value, per LiveRamp and Hightouch. This encourages autonomy by tying success to internal graph health, not just stakeholder happiness.

   - Metrics:

     - **Unified Profile Completion Rate**: Percentage of user profiles with â‰¥3 bridged identifiers (e.g., email \+ phone \+ device) that are validated as correct. Target: 80%+, linked to conversion lift (e.g., \+10% relevancy-driven sales).

     - **Bridge Impact Score**: Average lift in downstream metrics (e.g., \+X% in customer lifetime value from bridged profiles). Use proxy simulations for quick iterations.

     - NSM Fit: Strong for growth alignment; Amplitude notes similar metrics for identity products.

3. **Probabilistic Risk Approach** (Incorporates Modeled Error Rates)

   - Focus: Use statistical models to estimate collision risks before bridging, per TigerGraph's best practices. This minimizes bad bridges proactively, speeding up cycles by automating decisions.

   - Metrics:

     - **Expected Collision Rate**: Predicted % of incorrect bridges based on identifier entropy (e.g., name density). Threshold: Reject if \>10%.

     - **Risk-Adjusted Bridge Volume**: (Correct Bridges) \- (Weighted Incorrect Bridges, e.g., weighted by severity like shared email collisions). Target growth: \+20% quarter-over-quarter.

     - NSM Fit: Good for autonomy; allows team to iterate on models independently.

4. **Iteration Speed \+ Quality Approach** (Addresses Slow Cycles Directly)

   - Focus: Blend velocity with accuracy to counter perfectionism, inspired by Artkai's North Star Framework.

   - Metrics:

     - **Bridge Iteration Velocity**: Number of validated bridge rule changes per 2-week cycle, with accuracy hold (e.g., maintain F1 \>0.8).

     - **Net Positive Bridge Rate**: (Correct \- Incorrect Bridges) / Total Attempts, tracked weekly.

     - NSM Fit: Enables self-direction; ties to business by correlating with faster product improvements.

5. **Downstream Impact Approach** (Links to Overall Business Objectives)

   - Focus: Measure indirect effects on growth/relevancy, per Finmark's NSM guide. Use A/B tests to attribute outcomes to identity improvements.

   - Metrics:

     - **Relevancy Lift from Identities**: % increase in personalized offer acceptance rates due to bridged profiles.

     - **Growth Attribution Score**: % of total conversions attributed to identity-bridged users (e.g., via multi-touch attribution).

     - NSM Fit: Excellent for alignment; but pair with internal metrics for autonomy.

**Recommendation**: Start with **F1 Score for Bridge Accuracy** as your NSMâ€”it's simple, aligns with your trade-off, and is widely used in identity resolution (e.g., by Segment and Equativ). Track it via automated sampling to reduce cycles to \<4 weeks. Research shows teams using such metrics see 20-30% faster iterations (per Heap.io).

---

### **Additional Suggestions for Testing Positive and Negative Identity Bridges**

Building on your three ideas, here are enhancements drawn from sources like DataLadder, Census, and academic reviews (e.g., SpringerOpen). These focus on ML, probabilistic modeling, and data quality checks to handle edge cases.

1. **Your Idea: Increases in Joins with Consistent Attributes (e.g., Same First Name), Ignoring Edge Cases**

   - **Additional Suggestions**:

     - Use **Fuzzy Matching \+ Confidence Scoring**: Implement libraries like FuzzyWuzzy (in Python) or Dedupe.io to score attribute similarity (e.g., Levenshtein distance for names). Set a threshold (e.g., \>80% match on first/last name \+ zip) for positive bridges. For edge cases (e.g., gifting with shared phone), apply rules like "if transaction type \= gift AND mismatch in \>2 attributes, flag as negative." Validate via random sampling (5% of bridges) with human review.

     - **ML-Based Anomaly Detection**: Train a model (e.g., using scikit-learn Isolation Forest) on historical data to detect outliers like inconsistent purchase patterns. This ignores edge cases automatically if they fall below a probability threshold (e.g., \<95% consistency across records).

     - **Approach to Ignore Overwhelmingly Consistent Cases**: Use clustering (e.g., DBSCAN algorithm) to group records by attribute consistency; only scrutinize clusters with \<90% internal match rate. This speeds up validation.

2. **Your Idea: Modeled Error Rates for Identifiers (e.g., First/Last Name \+ Zip, Based on Density/Common Names)**

   - **Additional Suggestions**:

     - **Probabilistic Models**: Use Bayesian inference (e.g., via PyMC3 library) to estimate collision probability: P(collision) \= (name frequency in zip) \* (population density factor). Sources like Roqad suggest integrating U.S. Census data for name/zip densities. For common names (e.g., "John Smith"), inflate error by 2x.

     - **Thresholds**: Reject joins if error \>5% for high-stakes identifiers (e.g., email); \>10% for low-stakes (e.g., device ID). Test thresholds via simulation: Generate synthetic data (e.g., with Faker library) and measure false positives. Adjust based on business riskâ€”e.g., lower for growth-critical bridges.

     - **Approaches**: Incorporate external data like phonetic encoding (Soundex for names) to reduce density bias. Monitor via A/B: One group with strict thresholds (e.g., 5%), another relaxed (15%), and compare downstream relevancy.

3. **Your Idea: Comparing to Golden Datasets, Addressing Completeness Challenges**

   - **Additional Suggestions**:

     - **Ways to Approach Completeness**: Generate synthetic golden sets using tools like Synthetic Data Vault (SDV) or Faker, simulating real bridges (e.g., 3-4 emails per user). Partner with providers like Acxiom for partial real datasets, then augment with your data. Use subsets focused on "known complete" users (e.g., loyalty program members with verified multi-identifiers).

     - **Ideas for Checking Data Quality**: Apply consistency rules (e.g., all emails in a profile must share domain patterns). Use statistical validation: Compute entropy (diversity) of identifiers; flag if \< expected (e.g., via Shannon Entropy formula). Manual sampling (e.g., 1% audited by experts) \+ automated checks (e.g., duplicate detection with Pandas). Sources like CDP Institute recommend "golden record" building via survivorship rules (e.g., most recent data wins).

     - **Overcoming Bridge Gaps**: Start with "partial goldens" (e.g., validate only email-phone bridges), then expand iteratively. Use graph queries (e.g., in Neo4j) to simulate missing identifiers and test robustness.

---

### **Tenets for Preventing Bad Identity Clusters in the Graph**

Your two tenets are spot-on: Avoiding external codes reduces blind risks (per Equativ and AWS Neptune), and focusing on improvements enables speed (per Artkai). Here are **5 additional tenets**, synthesized from best practices in TigerGraph, Segment, and PuppyGraph:

1. **Privacy and Consent by Design**: Always prioritize user consent and data minimization (e.g., only bridge with explicit opt-ins). This prevents regulatory risks and bad clusters from unverified data.

2. **Scalability and Monitoring First**: Build with graph databases that support real-time queries; implement continuous auditing (e.g., daily error scans) to catch bad clusters early, ensuring the graph doesn't degrade over time.

3. **Bias Mitigation and Fairness**: Regularly audit for biases (e.g., over-bridging common names in dense zips) using fairness metrics like demographic parity. This avoids incorrect bridges in diverse populations.

4. **Data Quality as a Gatekeeper**: Enforce ingress rules (e.g., validate identifiers for format/completeness before bridging) to prevent garbage-in-garbage-out clusters.

5. **Collaborative but Autonomous Governance**: Define clear ownership (team manages core graph) but include lightweight stakeholder feedback loops (e.g., quarterly reviews) to balance alignment without slowing iterations.

These tenets can form a "Identity Graph Manifesto" for your team, promoting autonomy while ensuring business alignment.