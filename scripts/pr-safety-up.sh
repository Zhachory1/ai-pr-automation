#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
"$ROOT/scripts/validate-pr-safety-runtime.sh"
exec docker compose --profile pr-safety up -d --build --wait pr-safety-review-controller
