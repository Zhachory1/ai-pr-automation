#!/usr/bin/env python3
"""Prepare pinned read-only PR evidence for native Kanban reviewer cards."""
import argparse
from contextlib import closing
import importlib.util
import json
import os
import sqlite3
import stat
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, HERE / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENQUEUE = load("review_snapshot_enqueue", "hermes-pr-kanban-enqueue.py")
COUNCIL = load("review_snapshot_council", "hermes-pr-review-council.py")
LOCAL = load("review_snapshot_local", "hermes-pr-review-local.py")
GITHUB = load("review_snapshot_github", "hermes_pr_review_github.py")


def _claim_native(workspace):
    root = ENQUEUE.safe_dir(Path.home() / ".local/share/ai-pr-automation/pr-kanban")
    if (workspace.parent != root or workspace.is_symlink() or not workspace.is_dir()
            or workspace.stat().st_uid != os.geteuid() or any(workspace.iterdir())
            or os.environ.get("HERMES_KANBAN_BOARD") != "pr-review"
            or os.environ.get("HERMES_KANBAN_WORKSPACE") != str(workspace)
            or os.environ.get("HERMES_PROFILE") != "pr-review-v1"):
        raise ValueError("native review workspace is not bound to the operator")
    board = Path(os.environ.get("HERMES_KANBAN_DB", ""))
    task_id = os.environ.get("HERMES_KANBAN_TASK", "")
    if (not board.is_absolute() or board.is_symlink() or not board.is_file()
            or board.stat().st_uid != os.geteuid() or not ENQUEUE.TASK_ID.fullmatch(task_id)):
        raise ValueError("native review board or task is unavailable")
    with closing(sqlite3.connect(f"file:{board}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        task = conn.execute("SELECT body,assignee,status,workspace_kind,workspace_path,tenant,"
                            "idempotency_key,current_run_id,claim_lock,model_override,provider_override "
                            "FROM tasks WHERE id=?", (task_id,)).fetchone()
    try:
        request = json.loads(task["body"])
        encoded = ENQUEUE.canonical(request)
        expected = COUNCIL.identity("pr-review", request["repo"], request["number"], request["head_sha"])["operation_id"]
        valid = (type(request) is dict and set(request) ==
                 {"operation_id", "repo", "number", "url", "title", "head_sha"}
                 and type(request["number"]) is int and 0 < len(request["title"]) <= 256
                 and request["operation_id"] == expected == workspace.name
                 and request["url"] == f'https://github.com/{request["repo"]}/pull/{request["number"]}'
                 and encoded == task["body"].encode())
    except (TypeError, ValueError, KeyError, AttributeError) as error:
        raise ValueError("native review card identity is invalid") from error
    if (not valid or task["assignee"] != "pr-review-v1" or task["status"] != "running"
            or task["workspace_kind"] != "dir" or task["workspace_path"] != str(workspace)
            or task["tenant"] != expected or task["idempotency_key"] != expected
            or task["model_override"] != "gpt-6-sol" or task["provider_override"] != "openai-codex"
            or str(task["current_run_id"]) != os.environ.get("HERMES_KANBAN_RUN_ID")
            or not task["claim_lock"] or task["claim_lock"] != os.environ.get("HERMES_KANBAN_CLAIM_LOCK")):
        raise ValueError("native review card differs from pinned owner")
    workspace.chmod(0o700)
    ENQUEUE.safe_dir(workspace)
    ENQUEUE.immutable(workspace / "request.json", encoded)


def request_at(workspace):
    path = workspace / "request.json"
    if not path.exists() and not path.is_symlink():
        _claim_native(workspace)
    ENQUEUE.safe_dir(workspace)
    request = json.loads(ENQUEUE.read_immutable(path))
    if (not isinstance(request, dict) or set(request) !=
            {"operation_id", "repo", "number", "url", "title", "head_sha"}
            or type(request["number"]) is not int or request["operation_id"] != workspace.name
            or request["operation_id"] != COUNCIL.identity("pr-review", request["repo"],
                                                             request["number"], request["head_sha"])["operation_id"]
            or request["url"] != f'https://github.com/{request["repo"]}/pull/{request["number"]}'):
        raise ValueError("review workspace does not match exact head")
    return request


def _result_paths(workspace, spec, roles):
    paths = {role: str(workspace / "input" / f"{role}-result.json") for role in roles}
    board = Path(os.environ.get("HERMES_KANBAN_DB", ""))
    task_id = os.environ.get("HERMES_KANBAN_TASK", "")
    if not task_id:
        return paths
    if (not board.is_absolute() or board.is_symlink() or not board.is_file()
            or board.stat().st_uid != os.geteuid()):
        raise ValueError("review board is unavailable for specialist results")
    with closing(sqlite3.connect(f"file:{board}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        final = conn.execute("SELECT idempotency_key,assignee,status,body,model_override,provider_override "
                             "FROM tasks WHERE id=?", (task_id,)).fetchone()
        if final is None or final["idempotency_key"] != spec["workflow_id"] + ":final":
            return paths
        body = json.loads(final["body"])
        if (final["assignee"] != "pr-review-v1" or final["status"] != "running"
                or final["model_override"] != "gpt-6-sol" or final["provider_override"] != "openai-codex"
                or body.get("role") != "synthesis" or body.get("operation_id") != spec["operation_id"]
                or body.get("artifact_digest") != spec["artifact_digest"]):
            raise ValueError("synthesis result card differs")
        for role, path in paths.items():
            task = conn.execute("SELECT id,assignee,status,body FROM tasks WHERE idempotency_key=?",
                                (spec["workflow_id"] + ":" + role,)).fetchone()
            if (task is None or task["assignee"] != f"pr-review-{role}-v2" or task["status"] != "done"
                    or json.loads(task["body"]).get("operation_id") != spec["operation_id"]):
                raise ValueError("specialist result card differs")
            run = conn.execute("SELECT profile,outcome,metadata FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                               (task["id"],)).fetchone()
            if run is None or run["profile"] != task["assignee"] or run["outcome"] != "completed":
                raise ValueError("specialist result is incomplete")
            result = json.loads(run["metadata"])
            if (not isinstance(result, dict) or result.get("operation_id") != spec["operation_id"]
                    or result.get("role") != role or not isinstance(result.get("findings"), list)):
                raise ValueError("specialist result differs")
            result.pop("artifact_digest", None)
            COUNCIL._immutable_file(Path(path), ENQUEUE.canonical(result), max_bytes=65536)
    return paths


def prepare(workspace, github):
    request = request_at(workspace)
    state = github.state(request["repo"], request["number"])
    if state["state"] != "open" or state["head_sha"] != request["head_sha"]:
        raise ValueError("review head moved before snapshot preparation")
    manifest_path = workspace / "input/identity.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        manifest = json.loads(COUNCIL._immutable_file(manifest_path))
        spec = COUNCIL.plan({"repo": request["repo"], "number": request["number"],
                             **{key: manifest[key] for key in
                                ("operation_id", "head_sha", "base_sha", "diff_digest", "context_digest",
                                 "changed_paths", "repository_path")},
                             **({"unreviewable_paths": manifest["unreviewable_paths"]}
                                if "unreviewable_paths" in manifest else {})})
        if state["base_sha"] != spec["base_sha"] or spec["artifact_digest"] != manifest["artifact_digest"]:
            raise ValueError("review snapshot differs from live base")
        COUNCIL._verified_snapshot(workspace, spec)
    else:
        repository_path = LOCAL.resolve(request["repo"], request["head_sha"])
        if LOCAL.resolve(request["repo"], state["base_sha"]) != repository_path:
            raise ValueError("review base resolves to another checkout")
        snapshot, diff, context = COUNCIL.collect(github, request["repo"], request["number"],
                                                   request["head_sha"], repository_path)
        spec = COUNCIL.plan(snapshot)
        COUNCIL.prepare_snapshot(workspace, spec, diff, context)
    roles = [role for role in spec["specialists"] if role != "generalist"]
    workspaces = {role: str(COUNCIL.prepare_role_workspace(spec, workspace, role))
                  for role in (*roles, "synthesis")}
    return {"workflow_id": spec["workflow_id"], "artifact_digest": spec["artifact_digest"],
            "head_sha": spec["head_sha"], "base_sha": spec["base_sha"],
            "repository_path": spec["repository_path"], "roles": roles, "workspaces": workspaces,
            "result_paths": _result_paths(workspace, spec, roles)}


def cleanup(workspace, review_id, event, github):
    request = request_at(workspace)
    if event not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
        raise ValueError("invalid review event")
    marker = f'<!-- ai-pr-automation head={request["head_sha"]} -->'
    author = github.actor()
    matches = [review for review in github.reviews_for_head(request["repo"], request["number"])
               if review["id"] == review_id and review["commit_id"] == request["head_sha"]
               and review["author"] == author and review["state"] == event and marker in review["body"]]
    if len(matches) != 1:
        raise ValueError("exact-head review effect is not verified")
    manifest = workspace / "input/identity.json"
    context = workspace / "input/context.json"
    receipt_path = workspace / "review-snapshot-cleanup.json"
    if manifest.exists():
        identity = json.loads(COUNCIL._immutable_file(manifest))
        artifact_digest = identity["artifact_digest"]
    else:
        receipt = json.loads(COUNCIL._immutable_file(receipt_path))
        artifact_digest = receipt["artifact_digest"]
    receipt = {"operation_id": request["operation_id"], "head_sha": request["head_sha"],
               "artifact_digest": artifact_digest, "review_id": review_id, "event": event}
    COUNCIL._immutable_file(receipt_path, json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode())
    ENQUEUE.fsync_dir(workspace)
    for attempt in range(10):
        try:
            redact_review_cards(request, artifact_digest)
            break
        except (ValueError, sqlite3.OperationalError) as error:
            if (attempt == 9 or isinstance(error, ValueError) and str(error) != "review child run differs before redaction"
                    or isinstance(error, sqlite3.OperationalError) and
                    getattr(error, "sqlite_errorcode", None) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)):
                raise
            time.sleep(1)
    for path in (*[workspace / "input" / f"{role}-result.json"
                   for role in ("reliability", "mvp", "security")], context, manifest):
        if path.exists() or path.is_symlink():
            COUNCIL._immutable_file(path)
            path.unlink()
            ENQUEUE.fsync_dir(workspace / "input")


def redact_review_cards(request, artifact_digest):
    board = Path(os.environ.get("HERMES_KANBAN_DB", ""))
    if not board.is_absolute() or board.is_symlink() or not board.is_file():
        raise ValueError("PR review board is unavailable for redaction")
    info = board.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError("PR review board is unsafe")
    roles = {"reliability": "pr-review-reliability-v2", "mvp": "pr-review-mvp-v2",
             "security": "pr-review-security-v2"}
    prefix = "pr-review-council-" + artifact_digest[:32] + ":"
    with sqlite3.connect(f"file:{board}?mode=rw", uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id,idempotency_key,assignee,status,body,model_override,provider_override FROM tasks "
                            "WHERE idempotency_key LIKE ?", (prefix + "%",)).fetchall()
        found = []
        final_seen = False
        for task in rows:
            role = task["idempotency_key"].removeprefix(prefix)
            body = json.loads(task["body"])
            if role == "final":
                if (final_seen or task["assignee"] != "pr-review-v1" or task["status"] not in ("running", "blocked", "done")
                        or task["model_override"] != "gpt-6-sol" or task["provider_override"] != "openai-codex"
                        or body.get("role") != "synthesis"
                        or body.get("operation_id") != request["operation_id"]
                        or body.get("artifact_digest") != artifact_digest):
                    raise ValueError("final review card differs before redaction")
                final_seen = True
                continue
            if (role not in roles or task["assignee"] != roles[role]
                    or task["model_override"] != "gpt-6-luna" or task["provider_override"] != "openai-codex"
                    or task["status"] not in ("done", "archived")
                    or body.get("role") != role or body.get("operation_id") != request["operation_id"]
                    or body.get("artifact_digest") != artifact_digest):
                raise ValueError("review child lineage differs before redaction")
            runs = conn.execute("SELECT profile,outcome,ended_at,worker_pid FROM task_runs "
                                "WHERE task_id=? ORDER BY id", (task["id"],)).fetchall()
            if (not runs or any(run["profile"] != roles[role] or run["ended_at"] is None
                                or run["worker_pid"] is not None for run in runs)
                    or runs[-1]["outcome"] != "completed"
                    or sum(run["outcome"] == "completed" for run in runs) != 1):
                raise ValueError("review child run differs before redaction")
            if conn.execute("SELECT 1 FROM task_attachments WHERE task_id=?", (task["id"],)).fetchone():
                raise ValueError("review child attachment needs manual reconciliation")
            if conn.execute("SELECT 1 FROM task_comments WHERE task_id=? AND (author IS NULL OR author!=?)",
                            (task["id"], roles[role])).fetchone():
                raise ValueError("review child has operator comments requiring manual reconciliation")
            found.append((role, task["id"]))
        if not final_seen or {role for role, _ in found} not in ({"reliability", "mvp"},
                                                       {"reliability", "mvp", "security"}):
            raise ValueError("review child graph is incomplete")
        for _, task_id in found:
            conn.execute("UPDATE task_runs SET summary=NULL,metadata=NULL,error=NULL WHERE task_id=?", (task_id,))
            conn.execute("UPDATE task_comments SET body='[redacted]' WHERE task_id=?", (task_id,))
            conn.execute("UPDATE task_events SET payload=NULL WHERE task_id=?", (task_id,))
            conn.execute("UPDATE tasks SET result=NULL,last_failure_error=NULL WHERE id=?", (task_id,))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "cleanup"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--review-id", type=int)
    parser.add_argument("--event", choices=("APPROVE", "REQUEST_CHANGES", "COMMENT"))
    args = parser.parse_args()
    workspace = args.workspace
    github = GITHUB.GitHub()
    if args.mode == "prepare":
        print(json.dumps(prepare(workspace, github), sort_keys=True))
    elif args.review_id is None or args.review_id < 1 or args.event is None:
        raise ValueError("verified review ID and event are required for cleanup")
    else:
        cleanup(workspace, args.review_id, args.event, github)


if __name__ == "__main__":
    main()
