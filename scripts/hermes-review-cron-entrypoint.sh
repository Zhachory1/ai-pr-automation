#!/usr/bin/env bash
set -euo pipefail
[[ -s /run/secrets/github_discovery_token ]] \
  || { echo "configure review discovery token" >&2; exit 2; }
[[ "${PR_REVIEW_WORK_ROOT:-}" =~ ^/[A-Za-z0-9._/-]+$ ]] \
  || { echo "set an absolute host review work root" >&2; exit 2; }
schedule="${PR_REVIEW_CRON_SCHEDULE:-*/5 * * * *}"
[[ "$schedule" =~ ^([0-9*/,-]+\ ){4}[0-9*/,-]+$ ]] || { echo "invalid PR_REVIEW_CRON_SCHEDULE" >&2; exit 2; }
printf 'PATH=/usr/local/bin:/usr/bin:/bin\n' > /etc/cron.d/hermes-review
printf '%s root GH_TOKEN="$(cat /run/secrets/github_discovery_token)" PR_REVIEW_QUEUE_ENGINE=dashboard PR_REVIEW_WORK_ROOT="%s" /app/bin/hermes-pr-producer review >> /proc/1/fd/1 2>> /proc/1/fd/2\n' "$schedule" "$PR_REVIEW_WORK_ROOT" >> /etc/cron.d/hermes-review
chmod 0644 /etc/cron.d/hermes-review
exec cron -f
