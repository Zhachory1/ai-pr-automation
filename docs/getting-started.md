# Getting started (macOS)

This runtime uses the **current user's** `~/.hermes` installation. The separate `hermes-agent` service-account installer, Fleet Controller, request database, and host Kanban bridge are retired from default startup. Do not invoke `sudo scripts/hermes-native.sh install` or use cross-user Hermes calls to set up this fleet.

## Prerequisites

- Colima (or Docker Desktop) with Compose, Git, Python 3, Bash, and the Hermes CLI installed for your macOS account.
- A user-level Hermes gateway with `pr-review-v1` and `pr-maintain-v1` installed under `~/.hermes/profiles/`. Use the version-compatible definitions in `agent-config/hermes/profiles/`. The Kanban dispatcher runs within the gateway. The installed CLI must support `kanban create --body-file`, and `kanban show --json` must include `max_runtime_seconds`; this host uses v0.21.5 with the local-only body-file guard. Keep discovery paused if either contract is absent. Installing profiles does not authorize a model run.
- A repository authority YAML file with only approved `owner/repo` entries. The wildcard [example](../agent-config/hermes/authority.example.yaml) is **not** an operator grant. `fleet.sh` validates the source file and mirrors it into a Docker-readable private directory. It reads non-default `HERMES_AUTHORITY_SOURCE_FILE` and `HERMES_DOCKER_AUTHORITY_FILE` from exported shell variables, not from Compose `.env`; export both when overriding their default paths. Bare Compose requires `HERMES_AUTHORITY_FILE` explicitly and will not fall back to the example.
- TLS leaf certificate and key for the loopback HTTPS proxy, configured through `FLEET_CONTROLLER_TLS_CERT_FILE` and `FLEET_CONTROLLER_TLS_KEY_FILE` (legacy variable names). Existing valid material can be reused; [`scripts/generate-ui-tls.sh`](../scripts/generate-ui-tls.sh) creates a new set without overwriting one.

## Recreate the personal fleet on another Mac

Use an operator-owned account; do not use `sudo` or install the retired service-account jobs. These commands create no Signal receiver, GitHub submission, or model run. Stop any existing Hermes gateway before changing its runtime or profiles.

```bash
install -d -m 0700 "$HOME/.hermes"
RUNTIME="$HOME/.hermes/runtime-v0.21.5"
git clone https://github.com/NousResearch/hermes-agent.git "$RUNTIME"
git -C "$RUNTIME" checkout --detach f97608f178d1ffeca59860195ab7da295f7c8e5f
UV_PROJECT_ENVIRONMENT="$RUNTIME/venv" uv sync --project "$RUNTIME" --frozen --python 3.11 --extra anthropic --no-dev
bash scripts/hermes-personal-compat.sh --apply "$RUNTIME"
python3 scripts/personal-hermes-bootstrap.py --prepare
```

Install `uv`, Node.js 22+, `signal-cli` (tested with 0.14.8), and an authenticated `@rokt/zbrain@0.8.0` package before this sequence. The bootstrap copies only the eight public profile bundles, replacing the retired system memory-shim path with this checkout's user-owned shim. It writes three user LaunchAgents (dashboard, Signal, Kanban ingress) **without loading them**. It never replaces existing profiles or plists; inspect a reported conflict manually. The other 18 profiles (document writers, Council, and specialist roles) need operator-owned private profile material. Restore the 13 private source/writer/specialist profiles under `~/.hermes/profiles/<name>/` out of band, review machine-specific paths and `.env` files, then generate the five Council worker profiles **before starting the gateway**:

```bash
RUNTIME="$HOME/.hermes/runtime-v0.21.5"
install -d -m 0700 "$HOME/.hermes/pr-safety" "$HOME/.hermes/pr-safety/snapshots" "$HOME/.hermes/pr-safety/workflows"
python3 scripts/configure-hermes-kanban-profiles.py \
  --hermes-home "$HOME/.hermes" --service-user "$(id -un)" \
  --contract agent-config/hermes/workflows/pr-risk-council-kanban-v2.json \
  --personal --apply --snapshot-root "$HOME/.hermes/pr-safety/snapshots" \
  --workflow-root "$HOME/.hermes/pr-safety/workflows" \
  --worker-python "$RUNTIME/venv/bin/python"
python3 scripts/personal-hermes-bootstrap.py
python3 scripts/hermes-kanban-workflow-preflight.py \
  --hermes-home "$HOME/.hermes" --install-dir "$RUNTIME" \
  --contract agent-config/hermes/workflows/pr-risk-council-kanban-v2.json
```

The Council installer refuses existing worker targets rather than overwriting private state; if a backup already contains them, inspect and reconcile their config and `.env` locally instead of re-applying. The read-only preflight checks the exact worker-only tools, owner-only helper under `~/.hermes/bin/`, and pinned Kanban runtime. Private prompts, credentials, and MCP endpoints do not belong in this public repository. A passing inventory check is **not** authorization to run a private workflow.

For ZBrain evidence tools, use the sanitized [MCP example](../agent-config/hermes/personal-zbrain-mcp.example.yaml) when configuring each private profile; substitute this checkout, the installed `zbrain-mcp` executable, and the operator's private document root in the **private profile config**, not in Git. The repository's bridge converts ZBrain 0.8.0's framed responses into Hermes JSONL without editing or publishing ZBrain source. `node --test tests/test-hermes-mcp-jsonl-bridge.mjs` checks framing with a fake server and no private documents. Run `bash scripts/hermes-personal-compat.sh --check` to confirm Hermes' local-only body-file guard and Council `worker_only` MCP fencing before enabling work.

Supply your owner-only `~/.hermes/config.yaml`, provider credentials, repository authority, and Signal identity from private backup or configure them manually; no secrets or account state are generated here. Install the gateway with `"$RUNTIME/venv/bin/hermes" gateway install --no-start-now --no-start-on-login`; start it only after checking credentials and gateway multiplexing. The bootstrap's dashboard, Signal, and ingress plists are in `~/Library/LaunchAgents/`; `launchctl bootstrap "gui/$(id -u)" <plist>` can start a service immediately. Inspect each plist and check for a running receiver before loading it. Load the ingress before enabling discovery, and never load Docker Signal beside the user receiver. Restore board history from an owner-only offline backup **only after** stopping the old gateway and reconciling ready/running cards; starting two dispatchers or blindly restoring live cards can replay effects. Do not put board databases, private documents, account state, or runtime `.env` files in Git. Keep the old machine paused until handoff is verified.

Copy [`.env.example`](../.env.example) to `.env`, replace every `/Users/YOU` placeholder with the new account's home, and set `CODE_ROOT`, the Hindsight database password and provider settings, and the TLS paths. Keep `.env` and credential files outside commits. From a separate worktree, set `COMPOSE_ENV_FILES` to the operator `.env` before using `fleet.sh` or Compose. Export `HERMES_AUTHORITY_SOURCE_FILE="$HOME/.hermes/authority.yaml"` and `HERMES_DOCKER_AUTHORITY_FILE="$HOME/.hermes/runtime/authority.yaml"` before calling `fleet.sh`; Compose `.env` alone does not set these shell variables.

## Support-only startup

Stop old effect-producing controller/producers before switching an existing fleet; `support-up` does not stop orphans. Do not delete its request volume or unresolved rows. Then:

```bash
scripts/fleet.sh support-up
scripts/fleet.sh status
```

This starts Hindsight, Coderag, and nginx, **not** the PR cron producers or Signal. The personal Hermes gateway runs independently and remains up when `scripts/fleet.sh down` stops Compose. Manage Signal separately under the current user's `ai.hermes.signal` LaunchAgent. Stop any already-running Docker Signal container before starting the user receiver; `support-up` does not stop it. Do not target the Docker `signal` service or enable its `legacy-signal` profile while the user receiver owns port 18080. Check <http://hermes.localhost/health> without using a model or GitHub call. Compose nginx binds port 80 only on loopback; <https://fleet.localhost:8080/> opens the existing local Hermes Kanban dashboard; `dashboard.localhost` redirects there. The existing HTTPS `:8080` routes remain available. Starting Colima can resume previously running cron containers even with support-only startup; inspect them first. Neither support startup nor the proxy sends a Signal message.

## Opt into live PR discovery

Place a read-only GitHub discovery token and two different 64-hex per-kind ingress keys at the three file paths in `.env.example`. Keep each file owned by your user, mode `0600`, in an owner-only directory; `openssl rand -hex 32` generates one key without putting it in shell history. The personal bootstrap renders the host ingress LaunchAgent with paths under `~/.hermes/` (set `.env` to those same key paths). Set `HERMES_AUTHORITY_SOURCE_FILE` to `~/.hermes/authority.yaml` and `HERMES_DOCKER_AUTHORITY_FILE` to an operator-owned Docker-readable mirror before calling `fleet.sh`; these are exported shell variables, not parsed from `.env`. Review its plist and load it as a **user** LaunchAgent only after checking key paths and that no other ingress owns port 8767. The ingress listens on `127.0.0.1:8767` and accepts only authenticated, authority-scoped PR admission. Stop any legacy launchd PR producers before starting Compose crons. Configure provider and GitHub-effect credentials in your personal Hermes installation, not in the Compose containers. Review token scope, provider/data policy, and the current PR population before starting:

```bash
scripts/fleet.sh up     # starts both cron producers; can incur model costs and GitHub effects
scripts/fleet.sh pause  # stops PR discovery without stopping support services
```

Do not use `fleet.sh up`, a bare `docker compose up`, or a real PR submission as a read-only validation command. For inspection and preserved old request state, use the [operator runbook](operations.md). Document publication, memory writes, and PR-safety handoffs remain parked; this setup does not revive them.
