#!/usr/bin/env python3
"""Create or adopt one PRD round-zero graph through the supported Hermes CLI."""
import argparse, hashlib, json, os, pathlib, subprocess, sys, tempfile

BOARD, BOARD_NAME = "prd-write", "PRD Write"
ENGINES = ("fixed", "dynamic")
ROLES = ("root", "writer", "product-pm", "mvp", "occams-razor", "synthesis")
PROFILES = {"root": None, "writer": "prd-write-v1", "product-pm": "product-pm", "mvp": "mvp", "occams-razor": "occams-razor", "synthesis": "prd-write-v1"}
DYNAMIC_WRITER_CONTRACT = {
    "artifact": ["write UTF-8 Markdown with write_file inside scratch workspace", "require verified=true", "compute real SHA-256 with execute_code", "declare absolute path in kanban_complete artifacts", "never use kanban_attach or a placeholder digest"],
    "fanout": ["create product-pm, mvp, and occams-razor reviewers with current writer as parent", "assign roles to matching profiles exactly: product-pm to product-pm, mvp to mvp, occams-razor to occams-razor", "create one prd-write-v1 synthesis with writer and all reviewers as parents", "use prd-write:{operation}:{round}:{role} idempotency keys", "do not set task skills; every child body must be self-contained", "complete writer with every returned child ID in created_cards"],
    "reviewer_body": ["include workflow, operation, stage=reviewer, round, role, source filename, digest, and full role rubric", "read durable writer attachment from parent context with read_file", "return pass|revise|needs_human|deny plus blockers, advisories, attachment identity, and digest", "do not create tasks"],
    "synthesis_body": ["include workflow, operation, stage=synthesis, round, reviewer roles, source filename, digest, and these complete branch rules", "dedupe blockers and record owner role", "approve: block needs_input with final attachment identity", "revise below round 2: create exactly one prd-write-v1 writer with current synthesis as parent and a self-contained copy of this contract", "revision council always includes mvp and occams-razor plus unresolved blocker owners", "missing or malformed evidence, deny recommendation, or requested round 3: block needs_input", "on a resumed human block, validate and apply human_decision below before reviewer synthesis"],
    "human_decision": ["accept only newest comment prefixed human_decision_v1 whose author is exactly default", "require comment after latest needs_input block and before latest unblock event", "require matching operation and current attachment digest", "approve completes synthesis with metadata.status=approved", "revise requires non-empty reason and creates exactly one next writer subject to round cap", "deny requires non-empty reason and completes synthesis with metadata.status=denied", "invalid or stale decision re-blocks needs_input"],
}

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
    existing = run([*command, "kanban", "--board", BOARD, "list", "--tenant", operation, "--json"], env, True)
    contracts = set()
    for task in existing:
        try: body = json.loads(task.get("body", ""))
        except (AttributeError, json.JSONDecodeError): fail("existing operation has unknown task contract")
        if body.get("workflow") == BOARD and body.get("operation") == operation: contracts.add("dynamic")
        elif body.get("operation_id") == operation: contracts.add("fixed")
        else: fail("existing operation has unknown task contract")
    if contracts and contracts != {args.engine}: fail("operation already belongs to other workflow engine")
    if args.engine == "dynamic":
        body = canonical({
            "workflow": BOARD,
            "operation": operation,
            "stage": "writer",
            "round": 0,
            "role": "writer",
            "intake": request,
            "reviewer_roles": ["product-pm", "mvp", "occams-razor"],
            "contract": DYNAMIC_WRITER_CONTRACT,
            "output": "write verified PRD artifact, create required reviewers and synthesis, then complete with exact created_cards",
        }).decode()
        create = [*command, "kanban", "--board", BOARD, "create", request["title"], "--body", body, "--idempotency-key", f"{BOARD}:{operation}:0:writer", "--tenant", operation, "--max-runtime", "1800", "--max-retries", "1", "--completion-contract", "local-only", "--created-by", "operator", "--initial-status", "blocked", "--assignee", "prd-write-v1", "--json"]
        created = run(create, env, True)
        task_id = created.get("id") if isinstance(created, dict) else None
        status = created.get("status") if isinstance(created, dict) else None
        if not isinstance(task_id, str) or not task_id or not isinstance(status, str): fail("invalid task create result")
        if created.get("body") != body or created.get("assignee") != "prd-write-v1" or created.get("tenant") != operation or created.get("parents") not in ([], None) or created.get("skills") not in ([], None): fail("dynamic writer task mismatch")
        attachments = run([*command, "kanban", "--board", BOARD, "attachments", task_id, "--json"], env, True)
        if not attachments:
            with tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / "intake.json"; path.write_bytes(intake)
                run([*command, "kanban", "--board", BOARD, "attach", task_id, str(path), "--name", "intake.json", "--content-type", "application/json", "--author", "operator"], env)
        elif len(attachments) != 1 or attachments[0].get("filename") != "intake.json" or attachments[0].get("size") != len(intake) or attachments[0].get("content_type") != "application/json": fail("writer attachment mismatch")
        if status == "blocked":
            shown = run([*command, "kanban", "--board", BOARD, "show", task_id, "--json"], env, True)
            events, runs = shown.get("events"), shown.get("runs")
            initial = isinstance(events, list) and runs == [] and len(events) == 3 and events[0].get("kind") == "created" \
                and events[1].get("kind") == "blocked" and events[1].get("payload") == {"reason":"initial_status","status":"blocked","actor":"operator"} \
                and events[2].get("kind") == "attached" and events[2].get("payload", {}).get("filename") == "intake.json"
            if initial:
                run([*command, "kanban", "--board", BOARD, "unblock", task_id], env)
                if run([*command, "kanban", "--board", BOARD, "show", task_id, "--json"], env, True).get("task", {}).get("status") == "blocked": fail("task remained blocked")
        return {"board": BOARD, "operation_id": operation, "tasks": {"writer": task_id}}
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
        create = [*command, "kanban", "--board", BOARD, "create", request["title"] if role == "root" else f"PRD round 0: {role}", "--body", canonical(bodies[role]).decode(), "--idempotency-key", f"{BOARD}:{operation}:0:{role}", "--tenant", operation, "--max-runtime", "1800", "--max-retries", "1", "--completion-contract", "local-only", "--created-by", "operator", "--initial-status", "blocked", "--json"]
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
    parser = argparse.ArgumentParser(); parser.add_argument("--hermes-home", type=pathlib.Path, required=True); parser.add_argument("--hermes-bin", type=pathlib.Path, required=True); parser.add_argument("--engine", choices=ENGINES, default="fixed")
    try: print(json.dumps(enqueue(parser.parse_args()), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError) as error: raise SystemExit(f"Hermes PRD enqueue failed: {error}")
if __name__ == "__main__": main()
