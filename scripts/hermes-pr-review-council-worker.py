#!/usr/bin/env python3
"""One bounded host-local council pass; never run GitHub I/O in the ingress handler."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("review_council_ingress", HERE / "hermes-kanban-ingress.py")
INGRESS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(INGRESS)
local_spec = importlib.util.spec_from_file_location("review_local_checkout", HERE / "hermes-pr-review-local.py")
LOCAL = importlib.util.module_from_spec(local_spec)
local_spec.loader.exec_module(LOCAL)


def stale_pending(config, request, github):
    state = github.state(request["repo"], request["number"])
    if state["state"] == "open" and state["head_sha"] == request["head_sha"]:
        return None
    workspace = config.work / request["operation_id"]
    if ((workspace / "input/identity.json").exists() or (workspace / "review-effects.sqlite").exists()
            or (workspace / "snapshot/diff.patch").exists() or (workspace / "input/context.json").exists()):
        return None
    marker = f'<!-- ai-pr-automation head={request["head_sha"]} -->'
    if any(marker in (item.get("body") or "") for item in
           [*github.reviews_for_head(request["repo"], request["number"]),
            *github.comments(request["repo"], request["number"]) ]):
        raise ValueError("stale review head has an uncertain prior marker")
    if github.state(request["repo"], request["number"]) != state:
        raise ValueError("stale review head moved during reconciliation")
    outcome = {"status": "superseded", "operation_id": request["operation_id"],
               "head_sha": request["head_sha"], "task_id": None, "review_id": None}
    INGRESS.COUNCIL._immutable_file(workspace / "review-outcome.json",
                                    json.dumps(outcome, sort_keys=True, separators=(",", ":")).encode())
    INGRESS.ENQUEUE.fsync_dir(workspace)
    return {"status": "superseded", "operation_id": request["operation_id"]}


def tick(config):
    INGRESS.ENQUEUE.safe_dir(config.work)
    INGRESS.validate_council_runtime(config)
    lock_path = config.work / ".review-council-worker.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("unsafe council worker lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        failures = 0
        entries = sorted((entry for entry in config.work.iterdir()
                          if re.fullmatch(r"pr-review-[0-9a-f]{64}", entry.name)),
                         key=lambda entry: entry.stat().st_mtime_ns)
        for entry in entries:
            INGRESS.ENQUEUE.safe_dir(entry)
            path = entry / "request.json"
            if not path.exists() and not path.is_symlink():
                continue
            owner = json.loads(INGRESS.ENQUEUE.read_immutable(path))
            if not isinstance(owner, dict) or owner.get("route") != "council-v2":
                continue
            if set(owner) != {"route", "operation_id", "repo", "number", "head_sha", "title"} \
                    or owner["operation_id"] != entry.name:
                raise ValueError("council owner record differs")
            receipt = entry / "review-outcome.json"
            outcome = None
            if receipt.exists() or receipt.is_symlink():
                outcome = json.loads(INGRESS.COUNCIL._immutable_file(receipt))
                if not isinstance(outcome, dict) or outcome.get("operation_id") != entry.name:
                    raise ValueError("council outcome receipt differs")
                private = any((entry / name).exists() or (entry / name).is_symlink()
                              for name in ("input/identity.json", "input/context.json", "snapshot/diff.patch"))
                if not private and (outcome.get("status") == "superseded"
                                    or (entry / "review-cleanup.json").exists()):
                    continue
                if outcome.get("status") != "verified":
                    raise ValueError("superseded council cleanup incomplete")
            grants = INGRESS.AUTHORITY.load(config.authority)
            repo = owner["repo"]
            if repo not in grants and f"{repo.split('/', 1)[0]}/*" not in grants:
                raise ValueError("council repository is not granted")
            request = {key: owner[key] for key in ("operation_id", "repo", "number", "head_sha", "title")}
            request["url"] = f'https://github.com/{repo}/pull/{owner["number"]}'
            INGRESS.validate({key: request[key] for key in ("repo", "number", "url", "title", "head_sha")}, "pr-review")
            try:
                result = None if outcome else stale_pending(config, request, INGRESS.GITHUB.GitHub())
                if result:
                    return result
                source = None if outcome else LOCAL.resolve(request["repo"], request["head_sha"])
                return INGRESS.invoke_council(config, request, source)
            except (ValueError, OSError, TimeoutError):
                failures += 1
        return {"status": "failed" if failures else "idle", "failed": failures}
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    for name in ("work", "authority", "hermes-home", "hermes-install", "hermes-bin"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    config = INGRESS.Config(Path(args.work), Path(args.authority), Path(args.hermes_home),
                            Path(args.hermes_bin), {}, council_install=Path(args.hermes_install),
                            council_enabled=True)
    print(json.dumps(tick(config), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TimeoutError) as error:
        raise SystemExit(f"review council worker failed: {error}") from None
