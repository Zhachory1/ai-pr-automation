---
name: buildkite-cli
description: "Use the `bk` CLI to interact with Buildkite from the terminal. Covers builds, jobs, pipelines, agents, artifacts, clusters, and secrets. For anything not covered by a subcommand, use `bk api` as an escape hatch to call the REST API directly."
---

# Buildkite CLI (`bk`)

`bk` wraps the Buildkite REST API. Auth via `bk configure add --org <slug> --token <token>`. Many commands auto-resolve pipeline/build context from the current git repo; pass `-p <slug>` or `-p <org>/<slug>` to be explicit.

**Global flags** (all commands): `-y/--yes` skip prompts, `--no-input` non-interactive, `-o json|yaml|text` output format, `--debug` print API calls, `--no-pager`, `-q/--quiet`.

---

## Escape Hatch: `bk api`

For any operation not covered by a subcommand, call the REST API directly.

> **WRONG**: `bk api "organizations/rokt/pipelines/..."` — org is auto-prepended.
> **RIGHT**: `bk api "pipelines/..."`

Paths start _after_ `/v2/organizations/{org}/`:

```bash
bk api /pipelines/my-pipeline/builds/429/annotations
bk api /pipelines/my-pipeline/builds/429/jobs/<uuid>/env
bk api --method PUT /pipelines/my-pipeline/builds/429/jobs/<uuid>/retry
bk api --method PUT /pipelines/my-pipeline/builds/429/jobs/<uuid>/unblock --data '{}'
bk api --method POST /pipelines --data '{"name":"new","repository":"git@..."}'
bk api --analytics /suites          # Test Analytics endpoint
bk api --file query.graphql         # GraphQL
```

---

## Commands

### Builds

```
bk build list [flags]           # ls alias; --pipeline/-p, --state, --branch, --commit,
                                #   --since/--until (e.g. 1h), --duration (e.g. ">20m"),
                                #   --creator, --meta-data key=val, --limit 50/--no-limit
bk build view [<number>]        # defaults to most recent; -b branch, -u user, --mine, -w web
bk build create                 # -b branch, -c commit, -m msg, -e KEY=VAL, -M key=val, -w
bk build watch [<number>]       # live polling; --interval secs (default 1)
bk build rebuild [<number>]     # -b, -u, --mine, -w
bk build cancel <number>        # -w
bk build download [<number>]    # download build resources
```

### Jobs

```
bk job list [flags]             # ls; -p, --state, --queue, --duration, --since/--until,
                                #   --order-by start_time|duration, --limit/--no-limit
bk job log <uuid>               # -p pipeline, -b build-number (required); --no-timestamps
bk job retry <uuid>
bk job cancel <uuid>            # -w
bk job unblock <uuid>           # --data '{"field":"value"}'; use '{}' when the block step has no form fields
bk job reprioritize <uuid> <n>  # priority alias; -p, -b build-number
```

### Pipelines

```
bk pipeline list                # -n name (partial), -r repo (partial), --limit
bk pipeline view [<pipeline>]   # -w
bk pipeline validate            # local only, no token; -f file(s)
bk pipeline convert             # migrate alias; -F file, -v vendor, -o output
                                #   vendors: github, bitbucket, circleci, jenkins, gitlab, harness, bitrise
bk pipeline copy [<pipeline>]   # cp alias; -t target, -c cluster, --dry-run
bk pipeline create <name>
bk init                         # scaffold .buildkite/pipeline.yaml
```

### Agents

```
bk agent list                   # --state running|idle|paused, --hostname, --tags key=val (all must match),
                                #   --name, --version, --limit
bk agent view <uuid>            # -w
bk agent stop [<uuid>...]       # --force (kills in-progress jobs); accepts stdin (one id per line)
bk agent pause <uuid>
bk agent resume <uuid>
```

### Artifacts

```
bk artifacts list [<number>]    # ls; -p, -j job-uuid
bk artifacts download <uuid>
```

### Clusters & Secrets

```
bk cluster list / view <id>
bk secret list   --cluster-id=<id>
bk secret get    --cluster-id=<id> --secret-id=<id>
bk secret create --cluster-id=<id> --key=<key>
bk secret update --cluster-id=<id> --secret-id=<id>
bk secret delete --cluster-id=<id> --secret-id=<id>    # rm alias
```

### Auth & Config

```
bk configure add [--org <slug>] [--token <token>]
bk use [<org-slug>]
bk whoami
bk config list [--local|--global] / get <key> / set <key> <val> / unset <key>
bk organization list            # org ls alias
```

---

## Common Workflows

```bash
# Investigate a failed build
bk build view 429 -p my-pipeline
bk job list -p my-pipeline --state failed --since 1h -o json
bk job log <uuid> -p my-pipeline -b 429 --no-timestamps

# Watch live (built-in, 1s poll)
bk build watch -p my-pipeline

# Watch live with job-level state table (30s poll, preferred for long builds)
# Use scripts/monitor-build.sh from this skill — copy it to the repo or run directly:
bash /path/to/skill/scripts/monitor-build.sh <pipeline-slug> <build-number> [interval-secs]
# e.g.
bash ~/.claude/skills/buildkite/scripts/monitor-build.sh deploy-kit 2309 30
# Exits 0 on passed, 1 on failed/canceled. Prints ✅/❌/🔄/⏳/🔒/💔 per job each cycle.

# Trigger a build
bk build create -p my-pipeline -b feature-x -e "FOO=bar" -M "key=val" -w

# Retry
bk job retry <uuid>
bk build rebuild 429 -p my-pipeline

# Artifacts
bk artifacts list 429 -p my-pipeline
bk artifacts download <uuid>

# Validate pipeline YAML (no token)
bk pipeline validate -f .buildkite/pipeline.yaml

# Stop idle agents
bk agent list --state idle -o json | jq -r '.[].id' | bk agent stop

# Annotations / env vars (no subcommand — use escape hatch)
bk api /pipelines/my-pipeline/builds/429/annotations | jq .
bk api /pipelines/my-pipeline/builds/429/jobs/<uuid>/env | jq '.env'
```

---

## Notes

- `-o json` + `jq` for scripting; `--yes --no-input` for automation.
- `--duration` operators: `>`, `<`, `>=`, `<=`; units `s`/`m`/`h`; quote the value: `">20m"`.
- `--since`/`--until` are server-side (fast); `--duration`/`--message` are client-side (fetches then filters).
- `--no-limit` fetches all pages; can be slow on large orgs.
- Some unblock endpoints reject an empty request body with `400 Body should be a JSON Hash`; pass `--data '{}'` when no unblock fields are required.
