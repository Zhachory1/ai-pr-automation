# Production Operations: Readiness, Monitoring, and Troubleshooting

> **Source**: go/dev-handbook
> **Related files**: [incident-response.md](incident-response.md) (incident handling), [service-ownership.md](service-ownership.md) (observability requirements), [standards-paved-road.md](standards-paved-road.md) (observability baseline)

This section provides practical guidance for running services in production. The primary source of truth for incident management is the Incident Response Jumpkit (go/jumpkit).

## Production Readiness Checklist

Before a service reaches customers, it must be observable and operable. The following must be in place:

* Runbook exists and is linked from the PR/ticket
* SLOs/SLIs are defined, alerts are wired, and dashboards are reviewed with on-call
* Rollback plan and owner are explicit
* Tests are executed and passing
* Security/privacy review is complete (auth, PII, data flows, logging)
* Access is least-privilege with MFA; threat modeling and secure-coding checks are complete
* Dashboards reviewed; logs/metrics flowing to standard tools with access controls
* Security gates completed (threat model as applicable; code and dependency scanning; secrets management; audit trail)

Backups live only in approved cloud environments with appropriate encryption and retention; removable media for backup is not permitted.

## Monitoring and Alerting

Teams send metrics, logs, and traces to the shared stack (Datadog for metrics/monitoring; Observe for logging/tracing; Chronosphere for additional monitoring) and stand up dashboards that make golden signals obvious.

After go-live:

* 4-hour and 24-hour health checks are complete
* Any Sentinel anomalies have been triaged and tickets updated
* Performance (TTI/RTTI) has been verified on key pages
* Alerts are noise-tuned; rollback plan validated

Audit logging covers authenticated access attempts, configuration changes, cloud audit events, and application-level actions so we can reconstruct what happened and why.

## Troubleshooting

Engineers troubleshoot using standardized logs, traces, and dashboards from our observability stack. Use Datadog, Chronosphere, and Observe to read logs, traces, and metrics. Common patterns:

* Start with dashboards to understand golden signals (latency, errors, saturation, traffic)
* Use traces to follow requests through the system
* Consult runbooks for known issues and remediation steps
* Check recent deployments and changes that might correlate with issues

If signals are missing, we add them as part of remediation so future incidents are faster to resolve. Logging and monitoring policies exist to ensure we record the events needed for investigation and accountability.
