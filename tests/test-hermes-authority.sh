#!/usr/bin/env bash
# The YAML authority allowlist is scope-of-attention, not security. Verify it lists granted repos,
# checks membership, rejects malformed repos, and denies (exit 3) an ungranted repo.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

cat > "$tmp/authority.yaml" <<'EOF'
# operator grant of repo space (permission intent, not a proof)
repos:
  - Zhachory1/ai-pr-automation
  - Zhachory1/other-repo   # trailing comment ignored
EOF

list="$(scripts/hermes-authority.py --file "$tmp/authority.yaml")"
[[ "$(jq -r '.repos|join(",")' <<<"$list")" == "Zhachory1/ai-pr-automation,Zhachory1/other-repo" ]] \
  || { echo "FAIL: list: $list" >&2; exit 1; }

scripts/hermes-authority.py --file "$tmp/authority.yaml" --check Zhachory1/ai-pr-automation >/dev/null \
  || { echo 'FAIL: granted repo not accepted' >&2; exit 1; }

if scripts/hermes-authority.py --file "$tmp/authority.yaml" --check evil/repo >/dev/null 2>&1; then
  echo 'FAIL: ungranted repo accepted' >&2; exit 1
fi
rc=0; scripts/hermes-authority.py --file "$tmp/authority.yaml" --check evil/repo >/dev/null 2>&1 || rc=$?
[[ "$rc" == 3 ]] || { echo "FAIL: deny exit code was $rc, expected 3" >&2; exit 1; }

# Malformed repo entry is rejected loudly, not silently accepted.
cat > "$tmp/bad.yaml" <<'EOF'
repos:
  - not a repo
EOF
if scripts/hermes-authority.py --file "$tmp/bad.yaml" >/dev/null 2>&1; then
  echo 'FAIL: malformed repo accepted' >&2; exit 1
fi

# Missing file fails loudly.
if scripts/hermes-authority.py --file "$tmp/nope.yaml" >/dev/null 2>&1; then
  echo 'FAIL: missing file accepted' >&2; exit 1
fi

echo 'PASS: YAML authority allowlist lists, checks, and rejects correctly'
