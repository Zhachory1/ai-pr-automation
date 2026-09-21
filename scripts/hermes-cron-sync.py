#!/usr/bin/env python3
"""Declaratively sync the host-native executor cron jobs into Hermes' built-in scheduler.

Each queue role runs as a `--no-agent` cron job whose script is the installed executor
(hermes-queue-runner / hermes-memory-curate). The executor claims one request, invokes the
role profile itself, and settles; the cron layer only schedules ticks. Jobs are created PAUSED
so no paid call fires until the operator resumes a specific role. Sync is idempotent: it creates
missing jobs, never edits or resumes an existing one, and reports drift.

Run as the hermes-agent service account (it owns ~/.hermes and the launcher).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERMES = os.environ.get("HERMES_BIN", str(Path.home() / ".local/bin/hermes"))
SCRIPTS_DIR = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "scripts"
SUPPORT_ROOT = os.environ.get("HERMES_NATIVE_SUPPORT_ROOT", "/usr/local/libexec/ai-pr-automation")

# role -> executor invocation + schedule. repo_scoped roles need the target repo listed in the YAML
# authority grant before their producer enqueues work; there is no enrollment or freshness gate.
# memory-curate is a scheduled sweep with no external producer, so its executor self-enqueues one row
# per tick via its own --enqueue flag.
JOBS = {
    "pr-review":     {"exec": "hermes-queue-runner pr-review",  "schedule": "every 15m", "repo_scoped": True},
    "pr-maintain":   {"exec": "hermes-queue-runner pr-maintain", "schedule": "every 15m", "repo_scoped": True},
    "memory-curate": {"exec": "hermes-memory-curate --enqueue",  "schedule": "every 6h",  "repo_scoped": False},
}
NAME_PREFIX = "ai-pr-automation-"


def hermes(*args, check=True):
    result = subprocess.run([HERMES, "cron", *args], capture_output=True, text=True, timeout=60)
    if check and result.returncode:
        raise SystemExit(f"hermes cron {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def existing_job_names():
    out = hermes("list", "--all").stdout
    return set(re.findall(r"Name:\s+(" + re.escape(NAME_PREFIX) + r"\S+)", out))


def wrapper_script(role, spec):
    """Write a tiny launcher under ~/.hermes/scripts that sources the service env and execs the
    installed executor. Kept as a thin file (not the executor itself) so the canonical root-owned
    binary stays the single source. The executor owns any self-enqueue (memory-curate --enqueue)."""
    path = SCRIPTS_DIR / f"{NAME_PREFIX}{role}.sh"
    body = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'set -a; . "${HERMES_HOME:-$HOME/.hermes}/.env"; set +a\n'
        f"exec {SUPPORT_ROOT}/{spec['exec']}\n"
    )
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != body:
        path.write_text(body)
        path.chmod(0o755)
    return path.name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="create missing paused jobs (default: dry run)")
    args = parser.parse_args()
    if not Path(HERMES).exists():
        raise SystemExit(f"hermes launcher not found at {HERMES}")

    have = existing_job_names()
    planned, created, refreshed, skipped = [], [], [], []
    for role, spec in JOBS.items():
        name = f"{NAME_PREFIX}{role}"
        # The wrapper script is our artifact and must always track the current binary/enqueue logic,
        # even for an existing job (create skips existing, so a stale wrapper would otherwise persist).
        if args.apply:
            before = (SCRIPTS_DIR / f"{name}.sh").read_text() if (SCRIPTS_DIR / f"{name}.sh").exists() else None
            script = wrapper_script(role, spec)
            if name in have and before != (SCRIPTS_DIR / f"{name}.sh").read_text():
                refreshed.append(name)
        if name in have:
            skipped.append(name)
            continue
        planned.append(name)
        if args.apply:
            hermes("create", "--no-agent", "--name", name, "--paused",
                   "--paused-reason",
                   ("grant the target repo in the authority YAML before resume" if spec["repo_scoped"]
                    else "awaiting operator resume"),
                   "--script", script, spec["schedule"])
            created.append(name)

    report = {
        "mode": "apply" if args.apply else "dry-run",
        "existing": sorted(skipped),
        "planned": sorted(planned),
        "created": sorted(created),
        "script_refreshed": sorted(refreshed),
        "note": "jobs are PAUSED; resume a role with `hermes cron resume <name>`. "
                "repo-scoped roles need the target repo in the authority YAML grant before resume.",
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
