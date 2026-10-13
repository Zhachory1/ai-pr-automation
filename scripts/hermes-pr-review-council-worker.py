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
import time

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
        rows = conn.execute("SELECT id,idempotency_key,assignee,body,status,workspace_path,"
                            "claim_lock,worker_pid,current_run_id,model_override,provider_override FROM tasks "
                            "WHERE idempotency_key LIKE ?", (spec["workflow_id"] + ":%",)).fetchall()
        expected = {**spec["specialists"], "synthesis":spec["synthesis"]["profile"]}
        indexed, bound = {}, {}
        for task in rows:
            body = json.loads(task["body"])
            role = body.get("role")
            expected_model = INGRESS.COUNCIL.MODELS.get(role) if "model" in body else "gpt-6-sol"
            if (role not in expected or role in indexed or task["assignee"] != expected[role]
                    or ("model" in body and body["model"] != expected_model)
                    or task["model_override"] != expected_model or task["provider_override"] != "openai-codex"
                    or task["idempotency_key"] != f'{spec["workflow_id"]}:{role}'
                    or body.get("operation_id") != spec["operation_id"]
                    or body.get("artifact_digest") != spec["artifact_digest"]
                    or task["workspace_path"] != str(workspace / "workspaces" / role)
                    or task["claim_lock"] is not None or task["worker_pid"] is not None
                    or task["current_run_id"] is not None
                    or task["status"] not in ("done", "archived", "blocked", "todo", "ready")):
                raise ValueError("stale council card is active or differs from pinned graph")
            runs = conn.execute("SELECT profile,status,outcome,ended_at,worker_pid FROM task_runs WHERE task_id=?",
                                (task["id"],)).fetchall()
            if task["status"] in ("todo", "ready"):
                if runs:
                    raise ValueError("stale council card run history is incomplete")
            elif task["status"] in ("archived", "blocked") and not runs:
                if not conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND kind='stale_gc_parked'",
                                    (task["id"],)).fetchone():
                    raise ValueError("stale council card run history is incomplete")
            elif (len(runs) != 1 or runs[0]["ended_at"] is None or runs[0]["worker_pid"] is not None
                  or (runs[0]["profile"] != expected[role]
                      and not (runs[0]["profile"] is None and runs[0]["outcome"] == "spawn_failed"))
                  or (task["status"] == "done" and (runs[0]["status"], runs[0]["outcome"]) != ("done", "completed"))
                  or (task["status"] == "blocked" and runs[0]["outcome"] not in
                      ("blocked", "gave_up", "timed_out", "spawn_failed", "crashed", "reclaimed"))):
                raise ValueError("stale council card run history is incomplete")
            if conn.execute("SELECT 1 FROM task_attachments WHERE task_id=?", (task["id"],)).fetchone():
                raise ValueError("stale council card has attachments requiring manual reconciliation")
            if conn.execute("SELECT 1 FROM task_comments WHERE task_id=? AND author!=?",
                            (task["id"], expected[role])).fetchone():
                raise ValueError("stale council card has operator comments requiring manual reconciliation")
            indexed[role] = task["id"]
            bound[task["id"]] = (task["body"], expected_model)
        if set(indexed) != set(expected):
            raise ValueError("stale council graph is incomplete")
        for role in ("synthesis", *spec["specialists"]):
            task_id = indexed[role]
            current = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if current is None or current["status"] not in ("done", "archived", "blocked", "todo", "ready"):
                raise ValueError("stale council card became active during cleanup")
            if current["status"] in ("todo", "ready"):
                with conn:
                    parked = conn.execute("UPDATE tasks SET status='blocked' WHERE id=? AND status=? "
                                          "AND claim_lock IS NULL AND worker_pid IS NULL AND current_run_id IS NULL",
                                          (task_id, current["status"]))
                    if parked.rowcount != 1:
                        raise ValueError("stale council card became active during cleanup")
                    conn.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,?,NULL,?)",
                                 (task_id, "stale_gc_parked", int(time.time())))
        conn.execute("BEGIN IMMEDIATE")
        with conn:
            for task_id in indexed.values():
                current = conn.execute("SELECT status,claim_lock,worker_pid,current_run_id,body,model_override,provider_override "
                                       "FROM tasks WHERE id=?", (task_id,)).fetchone()
                if (current is None or current["status"] not in ("done", "archived", "blocked")
                        or current["body"] != bound[task_id][0] or current["model_override"] != bound[task_id][1]
                        or current["provider_override"] != "openai-codex"
                        or any(current[key] is not None for key in ("claim_lock", "worker_pid", "current_run_id"))):
                    raise ValueError("stale council card became active during redaction")
                if conn.execute("SELECT 1 FROM task_runs WHERE task_id=? AND ended_at IS NULL", (task_id,)).fetchone():
                    raise ValueError("stale council run reopened during cleanup")
            for task_id in indexed.values():
                conn.execute("UPDATE task_runs SET summary=NULL,metadata=NULL,error=NULL WHERE task_id=?", (task_id,))
                conn.execute("UPDATE task_comments SET body='[redacted]' WHERE task_id=?", (task_id,))
                conn.execute("UPDATE task_events SET payload=NULL WHERE task_id=?", (task_id,))
                conn.execute("UPDATE tasks SET result=NULL,last_failure_error=NULL WHERE id=?", (task_id,))


def stale_pending(config, request, github):
    state = github.state(request["repo"], request["number"])
    workspace = config.work / request["operation_id"]
    if (workspace / "review-effects.sqlite").exists() or (workspace / "review-effects.sqlite").is_symlink():
        raise ValueError("stale head has an unresolved GitHub effect")
    manifest_path = workspace / "input/identity.json"
    spec = None
    if manifest_path.exists() or manifest_path.is_symlink():
        manifest = json.loads(INGRESS.COUNCIL._immutable_file(manifest_path))
        spec = INGRESS.COUNCIL.plan({"repo":request["repo"], "number":request["number"],
            "operation_id":request["operation_id"], "head_sha":request["head_sha"],
            **{key:manifest[key] for key in ("base_sha","diff_digest","context_digest","changed_paths")},
            **({"repository_path":manifest["repository_path"]} if "repository_path" in manifest else {}),
            **({"unreviewable_paths":manifest["unreviewable_paths"]} if "unreviewable_paths" in manifest else {})})
        INGRESS.COUNCIL._verified_snapshot(workspace,spec)
        board_db = config.home / "kanban/boards/pr-review/kanban.db"
        if board_db.is_symlink():
            raise ValueError("stale council board is unsafe")
        has_cards = has_blocked = False
        if board_db.exists():
            with sqlite3.connect(f"file:{board_db}?mode=ro",uri=True) as conn:
                rows = conn.execute("SELECT status FROM tasks WHERE idempotency_key LIKE ?",
                                    (spec["workflow_id"]+':%',)).fetchall()
                has_cards = bool(rows)
                has_blocked = any(row[0] == "blocked" for row in rows)
    elif (workspace / "input/context.json").exists() or (workspace / "snapshot/diff.patch").exists():
        raise ValueError("stale head has incomplete private input")
    if (state["state"] == "open" and state["head_sha"] == request["head_sha"]
            and (spec is None or state["base_sha"] == spec["base_sha"])
            and not (spec and has_blocked)):
        return None
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
                source = None
                if not outcome:
                    state = INGRESS.GITHUB.GitHub().state(request["repo"], request["number"])
                    if state["state"] != "open" or state["head_sha"] != request["head_sha"]:
                        raise ValueError("review head changed before local checkout")
                    source = LOCAL.resolve(request["repo"], request["head_sha"])
                    if LOCAL.resolve(request["repo"], state["base_sha"]) != source:
                        raise ValueError("review base resolves to another checkout")
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
