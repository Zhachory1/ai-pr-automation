# Incident Response, Severity, and Post-Incident Reviews

> **Source**: go/dev-handbook
> **Related files**: [production-operations.md](production-operations.md) (production readiness), [service-ownership.md](service-ownership.md) (on-call expectations)

When an alert pages on-call, the first job is to establish impact and stop the bleeding safely. We rely on pre-agreed runbooks, known rollbacks, and feature flags rather than ad-hoc server changes. For on-call expectations, see [service-ownership.md](service-ownership.md).

## Incident Declaration

**Datadog Incidents**: Rokt uses Datadog Incidents as the platform for incident management. When an incident is identified (revenue impact, customer impact, monitoring failure, security compromise, or compliance failure), declare it in Datadog Incidents. The first responder becomes the **Incident Commander** (IC) and owns incident identification, communication, escalation, and coordination until resolution or handover.

## Severity Levels

Incidents are classified from SEV-1 through SEV-5. Use severity to guide your initial assessment and escalation decisions (go/jumpkit):

| Severity | Description | Business Impact | Internal Response | External Impact |
| :--- | :--- | :--- | :--- | :--- |
| SEV-1 | Critical incident with very high impact | Existential threat; potential for catastrophic financial and reputational loss | All hands on deck; executive team, legal, all relevant departments engaged | Affects all customers; public and shareholders notified |
| SEV-2 | Major incident with significant impact | Significant service outage; major disruption to business function | All hands on deck; coordination across RPD, Solutions, Success, GTM | Affects subset of customers; may require customer-centric post-mortems |
| SEV-3 | Minor incident with low impact | Noticeable revenue and customer impact; affects SLOs | Internal to RPD; requires cross-team coordination | Affects customers noticeably but not crippling; workarounds may exist |
| SEV-4 | Low-priority incident impacting single team | Minor customer impact; affects SLOs but contained | Internal to single engineering team | Minor inconvenience or performance degradation |
| SEV-5 | Informational or cosmetic issue | No direct customer or revenue impact | Internal to engineering team; resolved during working hours | No customer impact |

**SEV-1 Special Requirements:** Page a technical ExCo member, provide ExCo updates every 15 minutes, root cause analysis within 4 hours, comprehensive IR with remediation plan within 7 days.

For complete incident response procedures, see the Incident Response Jumpkit (go/jumpkit).

## Service Tiers

Service criticality is defined by tier classification (go/service-tiers):

| Tier | Description | Impact if Down | Examples |
| :--- | :--- | :--- | :--- |
| T0 | Core Platform—control-plane systems that other applications rely on | No system components will run | Kubernetes, Kafka, DNS/Networking |
| T1 | Business Critical—significant impact to customers or revenue | Disruption to public integrations | EcommerceAPI, Selector, Predictor, Bidding, Event API |
| T2 | Business Non-Critical—degraded experience but not total outage | Delayed impact on traffic quality (hours) | Signal Processor, Necromancer |
| T3 | Time Insensitive—no significant customer effect | Delayed impact (days) | ML Pipelines, Spark Jobs |

Tier expectations define availability targets (e.g., T0: 99.995% global, T1: 99.99%), scalability requirements, and recovery objectives. For complete tier expectations and classification guidance, see the Service Tier Classification document (go/service-tiers).

## Incident Management Process

1. **Assess & Declare**: Determine if an alert is an incident; declare it in Datadog Incidents
2. **Triage**: Use observability dashboards to assess impact, scope, and severity
3. **Communicate**: Update stakeholders on status and expected resolution; IC fills out incident fields in Datadog
4. **Mitigate**: Use runbooks, rollbacks, or feature flags to restore service
5. **Document**: Keep timeline actions updated in Datadog Incidents throughout
6. **Resolve**: Ship a proper fix via the pipeline; mark incident as resolved

If the issue involves sensitive access or identity, we use our access controls and SD-perimeter to constrain blast radius while we diagnose.

## Break-Glass Access

Break glass permissions are available to temporarily elevate a user's permissions to assist in debugging and doing battlefield triage to mitigate incident damage, but should only be relied on during an emergency. It is strongly discouraged from using break-glass for routine business operations.

Access to sensitive environments uses zero-trust connectivity with enforced MFA and least privilege. Systems are immutable and changed only through code and pipelines, which keeps configuration drift low and forensics clean.

## Shipping Fixes

All remediation is delivered via the pipeline. Code and infrastructure changes go through peer review, static and composition analysis, and then deploy with a clear rollback plan. Manual edits to servers are not allowed; infrastructure is treated as code and is auditable end-to-end.

Security is part of "done": we apply secure-coding guidance, threat modeling where appropriate, and post-deploy monitoring to verify that a fix actually resolves the issue and does not introduce new risk.

## Post-Incident Reviews

Post-incident reviews are concise and blameless. After resolution, we capture learnings and update systems accordingly. At Rokt, we strive for a blameless culture where we learn together from incidents to improve our services and drive towards lowering Mean Time To Recovery (MTTR).

**Post-mortem process**: The Incident Commander is responsible for generating an incident post-mortem. After marking an incident as "Resolved" in Datadog Incidents:

1. Create a placeholder document in the appropriate quarter's folder in Incident Post Mortems
2. Generate the post-mortem in Datadog using the "Rokt Post Mortem Template"
3. Download as Markdown and paste into the Google Doc
4. Review and remove any superfluous timeline information
5. Complete all sections including root cause analysis and follow-up actions

**Incident review meeting**: The IC schedules and presents the post-mortem within the timeframe defined by severity (e.g., SEV-1 requires a root cause analysis within 4 hours and comprehensive IR within 7 days). The IC invites their team, involved parties, and incidentreview@rokt.com to the meeting.

We focus on:

* What failed and why (root cause)
* How we can prevent recurrence
* Which guardrails or signals to add
* Updates needed to runbooks, dashboards, and alerts
* Tracking completion of follow-up tickets

Where vulnerabilities or configuration weaknesses are suspected, we schedule targeted security testing and hardening as follow-up, noting that we also conduct periodic independent assessments.

We update runbooks, dashboards, alerts, and (if behavior or definitions changed) the relevant canonical data or measurement documentation so downstream consumers remain correct by construction. Any changes to identity, attribution, or measurement are reflected in the canonical data/handbooks.

## Communication and Obligations

We communicate early and factually with internal stakeholders during incidents and keep a clear record of what we observed and changed. Where client data or systems may be affected, our contracts and data-processing agreements require timely notification and cooperation, as well as support for audits when requested; we maintain our operational evidence so we can meet these obligations without heroics.
