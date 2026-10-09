#!/usr/bin/env python3
"""One bounded host-local council pass; never run GitHub I/O in the ingress handler."""
import argparse
import fcntl
import importlib.util
import json
import os
import sqlite3
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


def gc_cards(config, spec, workspace):
    install = config.council_install
    if not install or not (install / "hermes_cli/kanban_db.py").is_file():
        raise ValueError("pinned council runtime unavailable for stale-card cleanup")
    if str(install) not in sys.path:
        sys.path.insert(0, str(install))
    from hermes_cli import kanban_db_connect as kbc
    with kbc.connect_closing(board=INGRESS.COUNCIL.BOARD) as conn:
        rows = conn.execute("SELECT id,idempotency_key,assignee,body,status,workspace_path FROM tasks "
                            "WHERE idempotency_key LIKE ?", (spec["workflow_id"] + ":%",)).fetchall()
        expected = {**spec["specialists"], "synthesis":spec["synthesis"]["profile"]}
        indexed = {}
        for task in rows:
            body = json.loads(task["body"])
            role = body.get("role")
            if (role not in expected or role in indexed or task["assignee"] != expected[role]
                    or task["idempotency_key"] != f'{spec["workflow_id"]}:{role}'
                    or body.get("operation_id") != spec["operation_id"]
                    or body.get("artifact_digest") != spec["artifact_digest"]
                    or task["workspace_path"] != str(workspace / "workspaces" / role)
                    or task["status"] not in ("done", "archived")):
                raise ValueError("stale council card is active or differs from pinned graph")
            runs = conn.execute("SELECT profile,status,outcome,ended_at,worker_pid FROM task_runs WHERE task_id=?",
                                (task["id"],)).fetchall()
            if (len(runs) != 1 or runs[0]["profile"] != expected[role] or runs[0]["status"] != "done"
                    or runs[0]["outcome"] != "completed" or runs[0]["ended_at"] is None
                    or runs[0]["worker_pid"] is not None):
                raise ValueError("stale council card run history is incomplete")
            if conn.execute("SELECT 1 FROM task_attachments WHERE task_id=?", (task["id"],)).fetchone():
                raise ValueError("stale council card has attachments requiring manual reconciliation")
            if conn.execute("SELECT 1 FROM task_comments WHERE task_id=? AND author!=?",
                            (task["id"], expected[role])).fetchone():
                raise ValueError("stale council card has operator comments requiring manual reconciliation")
            indexed[role] = task["id"]
        if set(indexed) != set(expected):
            raise ValueError("stale council graph is incomplete")
        with conn:
            for task_id in indexed.values():
                conn.execute("UPDATE task_runs SET summary=NULL,metadata=NULL,error=NULL WHERE task_id=?", (task_id,))
                conn.execute("UPDATE task_comments SET body='[redacted]' WHERE task_id=?", (task_id,))
                conn.execute("UPDATE task_events SET payload=NULL WHERE task_id=?", (task_id,))
                conn.execute("UPDATE tasks SET result=NULL,last_failure_error=NULL WHERE id=?", (task_id,))


def stale_pending(config, request, github):
    state = github.state(request["repo"], request["number"])
    if state["state"] == "open" and state["head_sha"] == request["head_sha"]:
        return None
    workspace = config.work / request["operation_id"]
    if (workspace / "review-effects.sqlite").exists() or (workspace / "review-effects.sqlite").is_symlink():
        raise ValueError("stale head has an unresolved GitHub effect")
    manifest_path = workspace / "input/identity.json"
    spec = None
    if manifest_path.exists() or manifest_path.is_symlink():
        manifest = json.loads(INGRESS.COUNCIL._immutable_file(manifest_path))
        spec = INGRESS.COUNCIL.plan({"repo":request["repo"], "number":request["number"],
            "operation_id":request["operation_id"], "head_sha":request["head_sha"],
            **{key:manifest[key] for key in ("base_sha","diff_digest","context_digest","changed_paths","repository_path")}})
        INGRESS.COUNCIL._verified_snapshot(workspace,spec)
        board_db = config.home / "kanban/boards/pr-review/kanban.db"
        if board_db.is_symlink():
            raise ValueError("stale council board is unsafe")
        has_cards = False
        if board_db.exists():
            with sqlite3.connect(f"file:{board_db}?mode=ro",uri=True) as conn:
                has_cards = bool(conn.execute("SELECT 1 FROM tasks WHERE idempotency_key LIKE ? LIMIT 1",
                                              (spec["workflow_id"]+':%',)).fetchone())
    elif (workspace / "input/context.json").exists() or (workspace / "snapshot/diff.patch").exists():
        raise ValueError("stale head has incomplete private input")
    marker = f'<!-- ai-pr-automation head={request["head_sha"]} -->'
    if any(marker in (item.get("body") or "") for item in
           [*github.reviews_for_head(request["repo"], request["number"]),
            *github.comments(request["repo"], request["number"]) ]):
        raise ValueError("stale review head has an uncertain prior marker")
    if github.state(request["repo"], request["number"]) != state:
        raise ValueError("stale review head moved during reconciliation")
    if spec and has_cards:
        gc_cards(config, spec, workspace)
    if spec:
        (workspace / "input/context.json").unlink()
        INGRESS.ENQUEUE.fsync_dir(workspace / "input")
        manifest_path.unlink()
        INGRESS.ENQUEUE.fsync_dir(workspace / "input")
    outcome = {"status": "superseded", "operation_id": request["operation_id"],
               "head_sha": request["head_sha"], "task_id": None, "review_id": None}
    INGRESS.COUNCIL._immutable_file(workspace / "review-outcome.json",
                                    json.dumps(outcome, sort_keys=True, separators=(",", ":")).encode())
    INGRESS.ENQUEUE.fsync_dir(workspace)
    return {"status": "superseded", "operation_id": request["operation_id"]}


def tick(config, *, stale_gc=False):
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
        failures = processed = cleaned = 0
        latest_status = "idle"
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
                if stale_gc:
                    if outcome and outcome.get("status") == "verified":
                        result = INGRESS.invoke_council(config, request)
                        processed += 1
                        latest_status = result["status"]
                    else:
                        result = stale_pending(config, request, INGRESS.GITHUB.GitHub())
                        cleaned += bool(result)
                    continue
                source = None if outcome else LOCAL.resolve(request["repo"], request["head_sha"])
                result = INGRESS.invoke_council(config, request, source)
                processed += 1
                latest_status = result["status"]
                if processed == 2:
                    break
            except (ValueError, OSError, TimeoutError) as error:
                failures += 1
                print(f"council {entry.name} failed: {type(error).__name__}", file=sys.stderr)
        return {"status": "failed" if failures else latest_status if processed else "superseded" if cleaned else "idle",
                "failed": failures, "processed": processed, "cleaned": cleaned}
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    for name in ("work", "authority", "hermes-home", "hermes-install", "hermes-bin"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--stale-gc", action="store_true", help="reconcile stale review heads on the daily pass")
    args = parser.parse_args()
    config = INGRESS.Config(Path(args.work), Path(args.authority), Path(args.hermes_home),
                            Path(args.hermes_bin), {}, council_install=Path(args.hermes_install),
                            council_enabled=True)
    result = tick(config, stale_gc=args.stale_gc)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    if result.get("failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TimeoutError) as error:
        raise SystemExit(f"review council worker failed: {error}") from None
