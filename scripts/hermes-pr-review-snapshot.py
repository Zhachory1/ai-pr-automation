#!/usr/bin/env python3
"""Prepare pinned read-only PR evidence for native Kanban reviewer cards."""
import argparse
import importlib.util
import json
import os
import sqlite3
import stat
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


def request_at(workspace):
    ENQUEUE.safe_dir(workspace)
    request = json.loads(ENQUEUE.read_immutable(workspace / "request.json"))
    if (not isinstance(request, dict) or set(request) !=
            {"operation_id", "repo", "number", "url", "title", "head_sha"}
            or type(request["number"]) is not int or request["operation_id"] != workspace.name
            or request["operation_id"] != COUNCIL.identity("pr-review", request["repo"],
                                                             request["number"], request["head_sha"])["operation_id"]
            or request["url"] != f'https://github.com/{request["repo"]}/pull/{request["number"]}'):
        raise ValueError("review workspace does not match exact head")
    return request


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
            "repository_path": spec["repository_path"], "roles": roles, "workspaces": workspaces}


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
    redact_review_cards(request, artifact_digest)
    for path in (context, manifest):
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
                if (final_seen or task["assignee"] != "pr-review-v1" or task["status"] != "running"
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
            runs = conn.execute("SELECT profile,outcome,ended_at FROM task_runs WHERE task_id=?",
                                (task["id"],)).fetchall()
            if len(runs) != 1 or tuple(runs[0])[:2] != (roles[role], "completed") or runs[0]["ended_at"] is None:
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
    workspace = ENQUEUE.safe_dir(args.workspace)
    github = GITHUB.GitHub()
    if args.mode == "prepare":
        print(json.dumps(prepare(workspace, github), sort_keys=True))
    elif args.review_id is None or args.review_id < 1 or args.event is None:
        raise ValueError("verified review ID and event are required for cleanup")
    else:
        cleanup(workspace, args.review_id, args.event, github)


if __name__ == "__main__":
    main()
