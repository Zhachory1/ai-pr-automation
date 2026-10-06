# Getting started (macOS)

This runtime uses the **current user's** `~/.hermes` installation. The separate `hermes-agent` service-account installer, Fleet Controller, request database, and host Kanban bridge are retired from default startup. Do not invoke `sudo scripts/hermes-native.sh install` or use cross-user Hermes calls to set up this fleet.

## Prerequisites

- Colima (or Docker Desktop) with Compose, Git, Python 3, Bash, and the Hermes CLI installed for your macOS account.
- A user-level Hermes gateway with `pr-review-v1` and `pr-maintain-v1` installed under `~/.hermes/profiles/`. Use the version-compatible definitions in `agent-config/hermes/profiles/`. The Kanban dispatcher runs within the gateway. The installed CLI must support `kanban create --body-file`, and `kanban show --json` must include `max_runtime_seconds`; this host uses v0.21.5 with the local-only body-file guard. Keep discovery paused if either contract is absent. Installing profiles does not authorize a model run.
- A repository authority YAML file with only approved `owner/repo` entries. The wildcard [example](../agent-config/hermes/authority.example.yaml) is **not** an operator grant. `fleet.sh` validates the source file and mirrors it into a Docker-readable private directory. It reads non-default `HERMES_AUTHORITY_SOURCE_FILE` and `HERMES_DOCKER_AUTHORITY_FILE` from exported shell variables, not from Compose `.env`; export both when overriding their default paths. Bare Compose requires `HERMES_AUTHORITY_FILE` explicitly and will not fall back to the example.
- TLS leaf certificate and key for the loopback HTTPS proxy, configured through `FLEET_CONTROLLER_TLS_CERT_FILE` and `FLEET_CONTROLLER_TLS_KEY_FILE` (legacy variable names). Existing valid material can be reused; [`scripts/generate-ui-tls.sh`](../scripts/generate-ui-tls.sh) creates a new set without overwriting one.

Copy [`.env.example`](../.env.example) to `.env`, set `CODE_ROOT`, the Hindsight database password and provider settings, and the TLS paths. Keep `.env` and credential files outside commits. From a separate worktree, set `COMPOSE_ENV_FILES` to the operator `.env` before using `fleet.sh` or Compose.

## Support-only startup

Stop old effect-producing controller/producers before switching an existing fleet; `support-up` does not stop orphans. Do not delete its request volume or unresolved rows. Then:

```bash
scripts/fleet.sh support-up
scripts/fleet.sh status
```

This starts Hindsight, Coderag, and nginx, **not** the PR cron producers or Signal. The personal Hermes gateway runs independently and remains up when `scripts/fleet.sh down` stops Compose. Manage Signal separately under the current user's `ai.hermes.signal` LaunchAgent. Stop any already-running Docker Signal container before starting the user receiver; `support-up` does not stop it. Do not target the Docker `signal` service or enable its `legacy-signal` profile while the user receiver owns port 18080. Check <http://hermes.localhost/health> without using a model or GitHub call. Compose nginx binds port 80 only on loopback; <http://dashboard.localhost/> opens the Kanban dashboard. The existing HTTPS `:8080` routes remain available. Starting Colima can resume previously running cron containers even with support-only startup; inspect them first. Neither support startup nor the proxy sends a Signal message.

## Opt into live PR discovery

Place a read-only GitHub discovery token and two different 64-hex per-kind ingress keys at the three file paths in `.env.example`. Keep each file owned by your user, mode `0600`, in an owner-only directory; `openssl rand -hex 32` generates one key without putting it in shell history. Render `launchd/com.example.ai-pr-automation-kanban-ingress.plist.template` with absolute paths for this checkout, the personal Hermes Python/CLI/home, the host authority file, keys, a private work root, and a private log directory; load it as a user LaunchAgent. The ingress listens on `127.0.0.1:8767` and accepts only authenticated, authority-scoped PR admission. Stop any legacy launchd PR producers before starting Compose crons. Configure provider and GitHub-effect credentials in your personal Hermes installation, not in the Compose containers. Review token scope, provider/data policy, and the current PR population before starting:

```bash
scripts/fleet.sh up     # starts both cron producers; can incur model costs and GitHub effects
scripts/fleet.sh pause  # stops PR discovery without stopping support services
```

Do not use `fleet.sh up`, a bare `docker compose up`, or a real PR submission as a read-only validation command. For inspection and preserved old request state, use the [operator runbook](operations.md). Document publication, memory writes, and PR-safety handoffs remain parked; this setup does not revive them.
