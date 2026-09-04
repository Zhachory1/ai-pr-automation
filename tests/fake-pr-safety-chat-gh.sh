#!/usr/bin/env bash
set -euo pipefail
case "$1 $2" in
  "pr view")
    jq -cn --arg url "https://github.com/${PR_SAFETY_TEST_REPO_NAME}/pull/7" --arg base "$PR_SAFETY_TEST_BASE" --arg head "$PR_SAFETY_TEST_HEAD" '{url:$url,baseRefOid:$base,headRefOid:$head}'
    ;;
  "repo clone")
    git clone --no-checkout "$PR_SAFETY_TEST_REPO" "$4" >/dev/null
    ;;
  *) exit 2 ;;
esac
