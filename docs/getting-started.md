# From zero to a running fleet (macOS)

This is the **host-native Hermes** deployment: Docker Compose holds the queue, producers, controller, and support services; a dedicated non-admin `hermes-agent` macOS account runs Hermes and its model/tool profiles. It is **not** the older Compose-embedded Hermes worker setup. Start only on a new host; upgrades and existing queued work need the drain/reconcile guidance in [Hermes operations](hermes/README.md#operations).

**Activation boundary:** `scripts/fleet.sh up` starts the host gateway and bridge, then the Compose controller and *review, maintenance, PR-safety, and memory* producers. Discovery may immediately enqueue existing eligible work; the controller can invoke a paid model and the host profiles can make GitHub changes. It is not a dry run or a review-only switch. Bare `scripts/compose.sh up -d --build` likewise starts the default producers/controller. Do not run either until the repository, provider, credential, and data-boundary checks below are approved. There is no documented/tested full-fleet idle `up` command that enables only one workflow.

## 1. Host and permissions

Install Docker Desktop with `docker compose`, Git, Python 3, Bash, `jq`, `psql`, `openssl`, and `gh` (the installer configures `gh` under the service account); you need administrator access for the Hermes installer and launchd. Create a dedicated **standard, non-admin** macOS account named `hermes-agent` using your organization's approved account procedure, with home `/Users/hermes-agent`. Verify `id hermes-agent` before installing. The install script checks this account; it does not create it. No `systemd` or Linux deployment is documented for this runtime.

Get a fresh repository checkout and work from its root:

```bash
git clone https://github.com/Zhachory1/ai-pr-automation.git
cd ai-pr-automation
docker compose version
```

After creating the service account, `id hermes-agent` must succeed. The privileged `sudo scripts/hermes-native.sh install` command downloads a pinned installer, writes root-owned support files and launchd plists, provisions profiles, and **removes retired host jobs/files**. Use it only after reviewing those effects; it is not a harmless validation command. Do not run it against an existing fleet with open Kanban attempts. See [host-native lifecycle](hermes/README.md#operations).

## 2. Choose scope, provider, and secrets before installing

Select repositories you may process. Repository authority YAML is a **scope-of-attention allowlist**, not the security boundary: configure just the approved `owner/repo` entries under `/usr/local/etc/ai-pr-automation/authority.yaml` rather than copying the wildcard entries in [`authority.example.yaml`](../agent-config/hermes/authority.example.yaml). Check it with `scripts/hermes-authority.py --file /usr/local/etc/ai-pr-automation/authority.yaml --check owner/repo`. The `fleet.sh up` command validates this file and mirrors it to a Docker-readable location. **PR-safety discovery has an additional OR condition**: an allowed `PR_SAFETY_ALLOWED_ORGS` match can admit merged PRs outside the authority's repository list when their author matches `PR_SAFETY_MERGED_PR_AUTHORS`. Do not assume a one-repo authority file limits PR-safety to one repository; approve the effective authors *and* organizations before any live `up`. If that scope is too broad, do not run `fleet.sh up` until a narrower activation path is built. GitHub branch protection, deploy-key scope, provider policy, and the service-account boundary remain necessary.

The host service account needs its **private** `/Users/hermes-agent/.hermes/.env` with a `GH_TOKEN` of at least 20 characters **before** `hermes-native.sh install`; [`configure-hermes-api.py`](../scripts/configure-hermes-api.py) reads that value, provisions the service account's GitHub CLI, and copies it to the Compose producer token file. `HERMES_API_KEYS_FILE` and `GITHUB_READ_TOKEN_FILE` must share the same private parent directory; the template defaults meet that requirement. Give that token only the approved repository permissions; do not assume the producer copy is independently read-only if the service token is write-scoped. Host Hermes provider credentials/OAuth and repository deploy keys are separate service-account setup; do not put those host secrets in this checkout or the Compose `.env` (Hindsight's *container* provider key belongs in `.env`). Confirm your organization permits selected repository/PR content and local private sources to reach each enabled provider and memory destination. Do not start the fleet until the required profiles can authenticate with approved provider credentials.

## 3. Configure Compose and Fleet Controller TLS

```bash
cp .env.example .env
chmod 600 .env
```

Replace machine-specific examples in [`.env.example`](../.env.example): `CODE_ROOT` (host-absolute directory), request/Hindsight DB passwords, keyed `HINDSIGHT_API_LLM_PROVIDER`/`HINDSIGHT_API_LLM_API_KEY` (the substrate retain test calls a paid provider), `SWARMVAULT_VAULT` (outside `CODE_ROOT`), UI identity, authority source/mirror, doc/handoff/snapshot/private-source paths, and file paths for the Hermes API key bundle, bridge keys, and producer token. For a first install, keep the template's `/Users/Shared/ai-pr-automation-runtime` and `/usr/local/etc/ai-pr-automation` host paths: `hermes-native.sh` and `fleet.sh` use defaults or *exported shell variables*, not your Compose `.env`, for their own host paths. If customizing those paths, set matching exported values for each host command and verify the resulting files before startup; a `.env` edit alone will not redirect the host installer. Choose `PR_REVIEW_QUEUE_ENGINE=postgres`, `PR_MAINTAIN_QUEUE_ENGINE=postgres`, `PR_SAFETY_QUEUE_ENGINE=postgres`, and `PR_SAFETY_ANALYSIS_ENGINE=single` for the documented default path. Inspect the PR-safety authors/orgs and memory sources as well; `fleet.sh up` starts their producers, not just the PR workflows. `fleet.sh` exports default PR-safety authors/orgs (`roktfleet,brucerokt` / `ROKT`) from its **shell environment**, overriding conflicting `.env` values in Compose. To customize those values, export approved nonempty values for the `fleet.sh up` invocation and inspect the effective configuration; an empty value is replaced by the script's default. Do not commit `.env`.

Before any `scripts/compose.sh up`, create Fleet Controller's session secret and CA/leaf TLS files **outside `CODE_ROOT` and any Git tree**. The repository already supplies a no-overwrite TLS generator; do not hand-roll a second CA:

```bash
install -d -m 700 "$HOME/.config/ai-pr-automation"
umask 077
openssl rand -hex 32 > "$HOME/.config/ai-pr-automation/fleet-controller-session-secret"
scripts/generate-ui-tls.sh
```

The generator writes TLS material to `$HOME/.config/ai-pr-automation-ui`, the directory in `.env.example` once `/Users/YOU` is replaced. Set all four `FLEET_CONTROLLER_*_FILE` paths in `.env` to the generated CA, leaf certificate, private key, and session secret; the session secret is in the *other* directory. The private key and session file must be owned by the operator (or root), mode `0600`, under an owner-only directory. The generator destroys the CA signing key and **prints** (but does not execute) the login-Keychain trust command. Trusting a CA is a separate operator decision. The generator refuses to overwrite an existing TLS directory; do not delete an existing CA just to rerun setup.

## 4. Install and inspect host runtime, without starting the fleet

With the service-account environment and approved authority ready, perform the privileged installation, then its preflight:

```bash
sudo scripts/hermes-native.sh install
sudo scripts/hermes-native.sh preflight
```

Installation provisions profile-scoped API keys and the bridge key at the host script's default paths (or matching *exported* path overrides); `.env` alone does not redirect the installer. The host account owns runtime credentials while Compose gets controller-readable copies. It does **not** authorize model calls or prove a full end-to-end PR. `scripts/hermes-native.sh sync-support` is not a dry run: it also replaces host support files and retires old jobs. If preflight fails, inspect the error rather than falling back to a broad `up`.

After installation has created the key files and your `.env` points at them, inspect Compose configuration **without dumping rendered secrets**:

```bash
scripts/compose.sh config --quiet
python3 scripts/validate-fleet-controller-secrets.py --env-file .env --repo .
```

The second command checks ownership, non-symlink paths, modes, certificate extensions and trust chain. `scripts/compose.sh` also runs this preflight on *all* `up`/`start`/`restart`/`run` actions, even when you select a single service.

## 5. Explicitly authorize live activation

Before running `scripts/fleet.sh up`, confirm all of the following:

- The authority file contains **only** intended repos **and** `PR_SAFETY_ALLOWED_ORGS` / `PR_SAFETY_MERGED_PR_AUTHORS` cover only an approved merged-PR population. Repository authority alone does not narrow PR-safety discovery. GitHub branch protection and deploy keys restrict effect scope. The `hermes-agent` account has provider and GitHub credentials approved for their roles.
- Review/maintenance PRs, merged-PR safety authors, memory source paths, and document inbox/stage destinations have been reviewed. Default producers are not optional merely because you did not visit their UI.
- Fleet Controller TLS files, producer token file (whose permissions match the host token), API-key bundle, and bridge keys are in approved private locations; database and Hindsight credentials are set.
- You accept discovery, provider billing, and potential GitHub writes now. Have an operator plan to watch the first requests and stop/reconcile unexpected effects.

Only then run the **live** command from the root checkout:

```bash
scripts/fleet.sh up
scripts/fleet.sh status
scripts/fleet.sh logs
```

`fleet.sh up` starts the host gateway/dashboard/bridge, runs an API conformance check, and starts default Compose controller/producers; it may also create or refresh service-account configuration and the Docker-readable authority/key copies. Open <https://fleet.localhost:8080> for the authenticated Fleet Controller (or <https://localhost:8080> for the UI landing page). Check the queue and the selected PR on GitHub: `status` and substrate checks alone cannot prove that a review posted correctly. To stop the fleet **without deleting named volumes**, use `scripts/fleet.sh down` after considering active attempts; verify remote effects before replaying any `reconcile` row. Never use `docker compose down -v` as routine shutdown.

For a first test, use a deliberately scoped repository and authorized PR in an environment approved for provider calls and GitHub writes. The [documentation plan](documentation-plan.md) leaves an independent clean-host trial unchecked; this prose has not been validated by a live installation.
