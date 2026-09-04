#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "$PR_SAFETY_TEST_CURL_ARGS"
cat "$PR_SAFETY_TEST_CURL_RESPONSE"
