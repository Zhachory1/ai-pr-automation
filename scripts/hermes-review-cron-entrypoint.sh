#!/usr/bin/env bash
set -euo pipefail
[[ -s /run/secrets/github_discovery_token && -s /run/secrets/pr_review_ingress_key ]] \
  || { echo "configure review discovery token and Kanban ingress key" >&2; exit 2; }
schedule="${PR_REVIEW_CRON_SCHEDULE:-*/5 * * * *}"
[[ "$schedule" =~ ^([0-9*/,-]+\ ){4}[0-9*/,-]+$ ]] || { echo "invalid PR_REVIEW_CRON_SCHEDULE" >&2; exit 2; }
printf 'PATH=/usr/local/bin:/usr/bin:/bin\n' > /etc/cron.d/hermes-review
printf '%s root GH_TOKEN="$(cat /run/secrets/github_discovery_token)" HERMES_AUTHORITY_FILE=/config/authority.yaml HERMES_AUTHORITY_BIN=/app/scripts/hermes-authority.py PR_REVIEW_QUEUE_ENGINE=bridge HERMES_KANBAN_INGRESS_KEY_FILE=/run/secrets/pr_review_ingress_key HERMES_KANBAN_INGRESS_URL="%s" /app/bin/hermes-pr-producer review >> /proc/1/fd/1 2>> /proc/1/fd/2\n' "$schedule" "${HERMES_KANBAN_INGRESS_URL:-http://host.docker.internal:8767}" >> /etc/cron.d/hermes-review
chmod 0644 /etc/cron.d/hermes-review
exec cron -f
