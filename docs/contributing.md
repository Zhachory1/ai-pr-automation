# Contributing

The supported deployment is host-native Hermes plus a Compose control plane. Read [architecture](architecture.md) before changing a trust boundary and [configuration](configuration.md) before adding a credential, mount, or environment variable. This repository contains Bash operator scripts, Python queue/controller code, SQL migrations, Compose, host launchd templates, and Hermes profiles; it has no single all-purpose build command.

| Concern | Source of truth | Focused checks to inspect/run when relevant |
| --- | --- | --- |
| Fleet start/stop, queue-engine switches | [`../scripts/fleet.sh`](../scripts/fleet.sh), [`../scripts/compose.sh`](../scripts/compose.sh) | `tests/test-hermes-compose-wiring.sh`, `tests/test-hermes-native-foundation.sh` |
| Native service account, plists, profile install | [`../scripts/hermes-native.sh`](../scripts/hermes-native.sh), [`../agent-config/hermes/`](../agent-config/hermes/) | `tests/test-hermes-native-foundation.sh`, `tests/test-hermes-api-pinned.sh` |
| Queue claims, attempts, replay, reconciliation | [`../lib/queue.sh`](../lib/queue.sh), [`../scripts/hermes-controller.py`](../scripts/hermes-controller.py), [`../docker/initdb/`](../docker/initdb/) | `tests/test-hermes-controller.py`, `tests/test-hermes-control-plane.sh`, `tests/test-schema-migrate-idempotent.sh` |
| GitHub discovery and authority | [`../bin/hermes-pr-producer`](../bin/hermes-pr-producer), [`../scripts/hermes-compose-producer.sh`](../scripts/hermes-compose-producer.sh), [`../scripts/hermes-authority.py`](../scripts/hermes-authority.py) | `tests/test-hermes-authority.sh`, `tests/test-hermes-pr-producer.sh`, `tests/test-hermes-queue-authority.sh` |
| PR safety and signed bridge | [`../bin/hermes-pr-safety-producer`](../bin/hermes-pr-safety-producer), [`../bin/hermes-kanban-safety-bridge`](../bin/hermes-kanban-safety-bridge) | `tests/test-hermes-pr-safety-producer.sh`, `tests/test-hermes-kanban-safety-bridge.py` |
| Document publication, memory, UI/TLS | [`../scripts/hermes-controller.py`](../scripts/hermes-controller.py), [`../bin/status-server`](../bin/status-server), [`../scripts/validate-fleet-controller-secrets.py`](../scripts/validate-fleet-controller-secrets.py) | `tests/test-hermes-doc-write-schema.sh`, `tests/test-hermes-memory-curate.sh`, `tests/test-status-server.py`, `tests/test-fleet-controller-auth.sh` |

For example, after a queue/controller change, run relevant focused tests from the repository root:

```bash
python3 tests/test-hermes-controller.py
bash tests/test-hermes-control-plane.sh
```

Read the specific test before running it: some use throwaway Postgres/Docker containers and need Docker available. These fake-provider tests are distinct from a live `scripts/fleet.sh up` or a GitHub/provider pilot. Do not use real `.env`, personal credentials, an active queue, or `sudo scripts/hermes-native.sh install` as a casual test fixture. For documentation-only changes, check affected file links and commands against current source, then use `git diff --check`; no service startup is required.

## Keep operating docs current

- Changes to startup, account setup, TLS, or credentials update [getting started](getting-started.md) and [configuration](configuration.md). Changes to network, mounts, route generations, and effects update [architecture](architecture.md). Changed failure/recovery behavior updates [operations](operations.md).
- Write down whether a command is **inspection**, **local state mutation**, or **live external effect**. A producer can queue work without running a model; a controller can later claim that work. Route changes and profile replacement require draining old attempts.
- Document what code enforces, not what an agent prompt hopes for. A repository authority YAML limits attention, not GitHub write authority; PR-safety also admits approved authors by allowed organization.
- Keep examples independent of one operator's home directory, avoid rendering secrets, and label historical PRDs/M0/M2 plans as such. [Hermes runtime docs](hermes/README.md) and [Compose substrate](../docker/README.md) contain detailed contracts.
- If a complete clean-host or restore trial was not run, leave it unverified in the [documentation plan](documentation-plan.md). Never quietly replace an operational check with a claim that tests exercised a provider or GitHub.
