#!/bin/sh
# One-shot fleet bootstrap: lock the shared Hindsight bank to a READ-ONLY MCP tool set.
#
# Why: review/maintain agents have direct Hindsight MCP `retain` access and (despite prompt rules)
# write review-completion noise into `fleet-shared` ("PR X reviewed with verdict Y", head SHAs, run
# ids). Prompt-only enforcement was measured to fail. Removing the `retain`/`sync_retain` tools from
# the bank at the server makes junk writes impossible while keeping `recall`/read fully functional.
# The pr-safety analyst bank is intentionally left writable (the analyst is meant to retain).
#
# Idempotent: PATCH auto-creates the bank config if absent and is safe to re-run on every `up`
# (including after a hindsight volume reset that recreated the bank).
set -eu

HS="${HINDSIGHT_URL:-http://hindsight:8888}"
BANK="${HINDSIGHT_SHARED_BANK:-fleet-shared}"

# Read-only allowlist: recall + read/introspection tools only. Every mutating tool (retain,
# sync_retain, create/update/delete/invalidate/clear/cancel, mental-model / directive / knowledge
# writes, update_bank, delete_bank) is intentionally excluded.
ALLOW='["recall","reflect","list_memories","get_memory","list_mental_models","get_mental_model","list_directives","list_documents","get_document","list_tags","get_bank","search_knowledge_base","get_knowledge_base_tree","get_knowledge_page","list_operations","get_operation"]'

log() { echo "hindsight-bank-init $*"; }

# Wait for hindsight to answer before configuring it.
i=0
until curl -fsS "$HS/health/ready" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -ge 60 ] && { log "FATAL: hindsight not ready at $HS after 60 tries"; exit 1; }
  sleep 2
done

patch_config() {
  curl -sS -o /tmp/hs-init.out -w '%{http_code}' -X PATCH "$HS/v1/default/banks/$BANK/config" \
    -H 'content-type: application/json' -d "{\"updates\":{\"mcp_enabled_tools\":$ALLOW}}"
}

log "locking bank '$BANK' to read-only MCP tools"
code="$(patch_config)"
# On the current image, PATCH on an absent bank auto-creates it (verified). If a future image 404s
# instead, create the bank explicitly and retry once, so a cold hindsight volume never deadlocks the
# agents that depend on this one-shot completing.
if [ "$code" = "404" ]; then
  log "bank '$BANK' absent; creating then retrying"
  curl -sS -o /dev/null -X POST "$HS/v1/default/banks" -H 'content-type: application/json' \
    -d "{\"bank_id\":\"$BANK\"}" || true
  code="$(patch_config)"
fi
[ "$code" = "200" ] || { log "FATAL: config PATCH returned $code: $(cat /tmp/hs-init.out)"; exit 1; }

# Verify POSITIVELY that the allowlist was applied: the echoed config must actually carry
# mcp_enabled_tools with recall present and retain/sync_retain absent. A response that silently
# dropped the field (API drift) must fail loud, not pass by omission.
applied="$(cat /tmp/hs-init.out)"
case "$applied" in
  *'"mcp_enabled_tools"'*) ;;
  *) log "FATAL: response has no mcp_enabled_tools (API drift?): $applied"; exit 1 ;;
esac
case "$applied" in
  *'"recall"'*) ;;
  *) log "FATAL: recall missing from applied allowlist: $applied"; exit 1 ;;
esac
case "$applied" in
  *'"retain"'*|*'"sync_retain"'*) log "FATAL: retain still present after PATCH: $applied"; exit 1 ;;
esac
log "done: '$BANK' MCP tools = $ALLOW"
