#!/usr/bin/env python3
"""Create or verify one fixed PR-safety graph through the supported Hermes CLI."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

TITLES = {role:f"Council {role} safety review" for role in
          ("review", "security", "reliability", "architecture")}
SYNTHESIS_TITLE = "Council safety synthesis"
BOARD = "pr-safety-council"
LOCK_FD = None


def load_module(path):
    spec = importlib.util.spec_from_file_location("risk_council", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(command, env, json_output=False):
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30,
                               pass_fds=() if LOCK_FD is None else (LOCK_FD,))
    if completed.returncode:
        raise ValueError(f"Hermes CLI failed: {completed.stderr.strip()[-300:]}")
    if not json_output:
        return completed.stdout.strip()
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("Hermes CLI returned invalid JSON") from error


def task(command, env, board, task_id):
    return run([*command, "kanban", "--board", board, "show", task_id, "--json"], env, True)


def create_task(command, env, board, title, body_path, profile, model, key, workspace, parents=(), *, tenant):
    args = [*command, "kanban", "--board", board, "create", title,
            "--body-file", str(body_path), "--assignee", profile, "--workspace", f"dir:{workspace}",
            "--idempotency-key", key, "--tenant", tenant, "--max-runtime", "900", "--max-retries", "1",
            "--model", model, "--provider", "anthropic", "--completion-contract", "local-only",
            "--created-by", "operator", "--initial-status", "blocked", "--json"]
    for parent in parents:
        args.extend(("--parent", parent))
    value = run(args, env, True)
    task_id = value.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Hermes CLI create omitted task id")
    return task_id


def verify_task(value, *, title, body, profile, model, workspace, parents, children, tenant):
    current = value.get("task")
    if not isinstance(current, dict) or current.get("title") != title or current.get("body") != body \
            or current.get("assignee") != profile or current.get("model_override") != model \
            or current.get("provider_override") != "anthropic" or current.get("workspace_kind") != "dir" \
            or current.get("workspace_path") != str(workspace) or current.get("max_runtime_seconds") != 900 \
            or current.get("max_retries") != 1 or current.get("completion_contract") != "local-only" \
            or current.get("created_by") != "operator" or current.get("tenant") != tenant \
            or sorted(value.get("parents", [])) != sorted(parents) \
            or sorted(value.get("children", [])) != sorted(children):
        raise ValueError("existing Kanban task differs from fixed safety graph")


def unblock(command, env, board, task_id):
    shown = task(command, env, board, task_id)
    current = shown["task"]["status"]
    if current == "blocked":
        operational_block = bool(shown.get("runs")) or any(
            event.get("kind") != "created" and not (
                event.get("kind") == "blocked" and event.get("payload") == {
                    "reason": "initial_status", "status": "blocked", "actor": "operator"})
            for event in shown.get("events", []))
        if operational_block:
            return "blocked"
        run([*command, "kanban", "--board", board, "unblock", task_id], env)
        current = task(command, env, board, task_id)["task"]["status"]
    if current not in {"todo", "ready", "running", "done", "archived"}:
        raise ValueError(f"unexpected task status after release: {current}")
    return current


def enqueue(args):
    request = json.loads(sys.stdin.read())
    request["nonce"] = hashlib.sha256(f"direct:{request.get('operation_id', '')}".encode()).hexdigest()[:32]
    council = load_module(args.risk_council)
    contract = json.loads(pathlib.Path(args.contract).read_text())
    home = pathlib.Path(args.hermes_home)
    ctx = council.v2_context(home, request, contract)
    council.profile_check_v2(home)
    prefix = f"{request['repo']}#{request['pr']}: "
    titles = {role:prefix + title for role, title in TITLES.items()}
    synthesis_title = prefix + SYNTHESIS_TITLE
    roles = {role:(council.V2_SPECIALISTS[role], council.V2_MODELS[role], titles[role])
             for role in TITLES}
    synthesis = (council.V2_SYNTHESIS, council.V2_MODELS["synthesis"], synthesis_title)
    board, tenant = BOARD, ctx["workflow_id"]
    env = {"HOME": str(home.parent), "HERMES_HOME": str(home), "PATH": os.environ.get("PATH", ""),
           "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "HERMES_SAFE_MODE": "1"}
    command = [str(args.hermes_bin)]

    boards = run([*command, "kanban", "boards", "list", "--json"], env, True)
    binding = ctx["root"] / "kanban-board.txt"
    if any(item.get("slug") == ctx["workflow_id"] for item in boards) or (
            ctx["root"].exists() and not binding.exists()):
        return {"status":"legacy", "board":ctx["workflow_id"],
                "workflow_id":ctx["workflow_id"], "task_ids":{}}
    council.private_dir(ctx["workflow_root"])
    council.private_dir(ctx["root"])
    council.immutable_file(binding, (board + "\n").encode())
    council.prepare_v2_inputs(ctx)
    council.verify_v2_inputs(ctx)
    if not any(item.get("slug") == board for item in boards):
        run([*command, "kanban", "boards", "create", board, "--name", "PR Safety Council"], env)
    boards = run([*command, "kanban", "boards", "list", "--json"], env, True)
    if len([item for item in boards if item.get("slug") == board]) != 1:
        raise ValueError("PR-safety board creation could not be verified")

    listing = [*command, "kanban", "--board", board, "list", "--tenant", tenant, "--archived", "--json"]
    existing = run(listing, env, True)
    by_title = {item["title"]: item for item in existing}
    if len(by_title) != len(existing) or not set(by_title) <= {*titles.values(), synthesis_title}:
        raise ValueError("PR-safety board contains unexpected or duplicate tasks")
    if len(existing) != 5:
        for item in existing:
            shown = task(command, env, board, item["id"])
            if shown["task"]["status"] != "blocked" or shown.get("runs"):
                raise ValueError("cannot add missing tasks to a released graph")

    bodies = ctx["root"] / "task-bodies"
    council.private_dir(bodies)
    tasks, body_values = {}, {}
    for role, (profile, model, title) in roles.items():
        body = council.v2_body(ctx, role)
        body_values[role] = body
        body_path = bodies / f"{role}.json"
        council.immutable_file(body_path, body.encode())
        tasks[role] = by_title[title]["id"] if title in by_title else create_task(
            command, env, board, title, body_path, profile, model,
            f"{ctx['workflow_id']}:{role}", ctx["root"], tenant=tenant)
    body = council.v2_body(ctx, "synthesis", tasks.values())
    body_values["synthesis"] = body
    body_path = bodies / "synthesis.json"
    council.immutable_file(body_path, body.encode())
    tasks["synthesis"] = by_title[synthesis_title]["id"] if synthesis_title in by_title else create_task(
        command, env, board, synthesis[2], body_path, synthesis[0], synthesis[1],
        f"{ctx['workflow_id']}:synthesis", ctx["root"], tasks.values(), tenant=tenant)

    listed = run(listing, env, True)
    if len(listed) != 5 or {item.get("id") for item in listed} != set(tasks.values()):
        raise ValueError("PR-safety board does not contain exact five-task graph")
    for role, (profile, model, title) in {**roles, "synthesis": synthesis}.items():
        expected_parents = list(tasks[other] for other in roles) if role == "synthesis" else []
        expected_children = [] if role == "synthesis" else [tasks["synthesis"]]
        verify_task(task(command, env, board, tasks[role]), title=title,
                    body=body_values[role], profile=profile, model=model,
                    workspace=ctx["root"], parents=expected_parents, children=expected_children, tenant=tenant)
        attachments = run([*command, "kanban", "--board", board, "attachments", tasks[role], "--json"], env, True)
        if attachments != []:
            raise ValueError("PR-safety task has attachments")

    unblock(command, env, board, tasks["synthesis"])
    for role in roles:
        unblock(command, env, board, tasks[role])
    return {"status": "enqueued", "board": board, "workflow_id": ctx["workflow_id"], "task_ids": tasks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", type=pathlib.Path, required=True)
    parser.add_argument("--hermes-bin", type=pathlib.Path, required=True)
    parser.add_argument("--risk-council", type=pathlib.Path, required=True)
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    args = parser.parse_args()
    global LOCK_FD
    descriptor = os.open(args.hermes_home / ".pr-safety-enqueue.lock",
                         os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Surviving CLI children retain the lock if the enqueue process crashes.
        LOCK_FD = lock.fileno()
        print(json.dumps(enqueue(args), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
