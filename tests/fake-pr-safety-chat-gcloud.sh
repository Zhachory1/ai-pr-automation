#!/usr/bin/env bash
set -euo pipefail
[[ "$*" == "auth application-default print-access-token" ]] || exit 2
printf 'refreshed-token\n'
