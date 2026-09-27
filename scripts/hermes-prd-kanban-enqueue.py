#!/usr/bin/env python3
"""Create or adopt one PRD round-zero graph through the supported Hermes CLI."""
import argparse, hashlib, json, os, pathlib, subprocess, sys, tempfile

BOARD, BOARD_NAME = "prd-write", "PRD Write"
ROLES = ("root", "writer", "product-pm", "mvp", "occams-razor", "synthesis")
PROFILES = {"root": None, "writer": "prd-write-v1", "product-pm": "product-pm", "mvp": "mvp", "occams-razor": "occams-razor", "synthesis": "prd-write-v1"}

def fail(message): raise ValueError(message)
def canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
def load_request():
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: fail(f"duplicate request key: {key}")
            value[key] = item
        return value
    raw = getattr(sys.stdin, "buffer", sys.stdin).read(); raw = raw.encode() if isinstance(raw, str) else raw
    try: value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: fail("invalid JSON number"))
    except json.JSONDecodeError as error: raise ValueError("invalid request JSON") from error
    if not isinstance(value, dict) or set(value) != {"operation_id", "title", "requester", "requirements"}: fail("request keys differ from contract")
    for key, limit in (("title", 256), ("requester", 200), ("requirements", 65536)):
        if not isinstance(value[key], str) or not value[key] or len(value[key]) > limit: fail(f"invalid {key}")
    if raw != canonical(value): fail("intake must be canonical JSON")
    core = {key: value[key] for key in ("title", "requester", "requirements")}
    if value["operation_id"] != "prd-" + hashlib.sha256(canonical(core)).hexdigest(): fail("operation ID differs from canonical request")
    return value, raw

def run(command, env, json_output=False):
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    if completed.returncode: fail(f"Hermes CLI failed: {completed.stderr.strip()[-300:]}")
    if not json_output: return completed.stdout.strip()
    try: return json.loads(completed.stdout)
    except json.JSONDecodeError as error: raise ValueError("Hermes CLI returned invalid JSON") from error

def enqueue(args):
    request, intake = load_request(); operation = request["operation_id"]
    env = {"HOME": str(args.hermes_home.parent), "HERMES_HOME": str(args.hermes_home), "PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "HERMES_SAFE_MODE": "1"}; command = [str(args.hermes_bin)]
    boards = run([*command, "kanban", "boards", "list", "--all", "--json"], env, True)
    board = next((item for item in boards if item.get("slug") == BOARD), None)
    if board is None: run([*command, "kanban", "boards", "create", BOARD, "--name", BOARD_NAME], env)
    elif board.get("name") != BOARD_NAME: fail("board name mismatch")
    full = {"operation_id": operation, "round": 0, "intake": request}; digest = "writer result attachment raw-byte SHA-256"
    bodies = {
        "root": {**full, "role": "root", "output": "remain blocked and unassigned; intake.json attachment is source of record"},
        "writer": {**full, "role": "writer", "output": "attach draft PRD bytes and raw-byte SHA-256 digest"},
        **{role: {"operation_id": operation, "round": 0, "role": role, "draft_digest": digest, "output": "attach review bound to draft digest"} for role in ROLES[2:5]},
        "synthesis": {"operation_id": operation, "round": 0, "role": "synthesis", "draft_digest": digest, "review_roles": list(ROLES[2:5]), "output": "attach verdict bound to digest and reviews; do not edit PRD bytes"},
    }
    tasks, statuses = {}, {}
    for role in ROLES:
        parents = [] if role in {"root", "writer"} else [tasks["writer"]] if role != "synthesis" else [tasks[name] for name in ROLES[2:5]]
        create = [*command, "kanban", "--board", BOARD, "create", request["title"] if role == "root" else f"PRD round 0: {role}", "--body", canonical(bodies[role]).decode(), "--idempotency-key", f"{operation}:{operation}:0:{role}", "--tenant", operation, "--max-runtime", "1800", "--max-retries", "1", "--completion-contract", "local-only", "--created-by", "operator", "--initial-status", "blocked", "--json"]
        if PROFILES[role]: create.extend(("--assignee", PROFILES[role]))
        for parent in parents: create.extend(("--parent", parent))
        created = run(create, env, True); task_id = created.get("id") if isinstance(created, dict) else None; status = created.get("status") if isinstance(created, dict) else None
        if not isinstance(task_id, str) or not task_id or not isinstance(status, str): fail("invalid task create result")
        tasks[role], statuses[role] = task_id, status
    attachments_command = [*command, "kanban", "--board", BOARD, "attachments", tasks["root"], "--json"]; attachments = run(attachments_command, env, True)
    if not attachments:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "intake.json"; path.write_bytes(intake)
            run([*command, "kanban", "--board", BOARD, "attach", tasks["root"], str(path), "--name", "intake.json", "--content-type", "application/json", "--author", "operator"], env)
    elif len(attachments) != 1 or attachments[0].get("filename") != "intake.json" or attachments[0].get("size") != len(intake) or attachments[0].get("content_type") != "application/json": fail("root attachment mismatch")
    for role in ("synthesis", *ROLES[2:5], "writer"):
        if statuses[role] != "blocked": continue
        shown = run([*command, "kanban", "--board", BOARD, "show", tasks[role], "--json"], env, True)
        events, runs = shown.get("events"), shown.get("runs")
        initial = isinstance(events, list) and runs == [] and len(events) == 2 and events[0].get("kind") == "created" \
            and events[1].get("kind") == "blocked" and events[1].get("payload") == {"reason":"initial_status","status":"blocked","actor":"operator"}
        if not initial: continue
        run([*command, "kanban", "--board", BOARD, "unblock", tasks[role]], env)
        status = run([*command, "kanban", "--board", BOARD, "show", tasks[role], "--json"], env, True).get("task", {}).get("status")
        if not isinstance(status, str) or status == "blocked": fail("task remained blocked")
    return {"board": BOARD, "operation_id": operation, "tasks": tasks}

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--hermes-home", type=pathlib.Path, required=True); parser.add_argument("--hermes-bin", type=pathlib.Path, required=True)
    try: print(json.dumps(enqueue(parser.parse_args()), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError) as error: raise SystemExit(f"Hermes PRD enqueue failed: {error}")
if __name__ == "__main__": main()
