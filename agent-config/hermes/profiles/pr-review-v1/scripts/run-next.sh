#!/usr/bin/env bash
set -euo pipefail
exec /usr/local/libexec/ai-pr-automation/hermes-queue-runner pr-review
