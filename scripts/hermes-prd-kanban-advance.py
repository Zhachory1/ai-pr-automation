#!/usr/bin/env python3
"""Advance one PRD graph through the supported Hermes CLI."""
import argparse, json, os, pathlib, re, subprocess

BOARD = "prd-write"
ROLES = {"root", "writer", "product-pm", "mvp", "occams-razor", "synthesis"}
PROFILES = {"writer":"prd-write-v1", "product-pm":"product-pm", "mvp":"mvp", "occams-razor":"occams-razor", "synthesis":"prd-write-v1"}
OPERATION = re.compile(r"prd-[0-9a-f]{64}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")

def fail(message): raise ValueError(message)
def canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value: fail(f"duplicate JSON key: {key}")
        value[key] = item
    return value

def decode(raw, label, canonical_required=False):
    if not isinstance(raw, str): fail(f"invalid {label}")
    try: value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: fail(f"invalid {label}"))
    except json.JSONDecodeError as error: raise ValueError(f"invalid {label}") from error
    if canonical_required and raw != canonical(value): fail(f"noncanonical {label}")
    return value

def run(command, env, json_output=False):
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    if completed.returncode: fail(f"Hermes CLI failed: {completed.stderr.strip()[-300:]}")
    if not json_output: return completed.stdout.strip()
    try: return json.loads(completed.stdout)
    except json.JSONDecodeError as error: raise ValueError("Hermes CLI returned invalid JSON") from error

def validate_blockers(value):
    if not isinstance(value, list) or len(value) > 100: fail("blocker ledger conflict")
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "owner", "status", "evidence"} or not isinstance(item["id"], str) or not item["id"] or item["id"] in seen or item["owner"] not in {"product-pm", "mvp", "occams-razor"} or item["status"] not in {"open", "resolved", "dismissed"} or not isinstance(item["evidence"], str) or not item["evidence"]: fail("blocker ledger conflict")
        seen.add(item["id"])

def selected(blockers):
    owners = {item["owner"] for item in blockers if item["status"] == "open"}
    return [role for role in ("mvp", "occams-razor", "product-pm") if role in {"mvp", "occams-razor"} | owners]

def synthesis_result(raw):
    value = decode(raw, "synthesis result")
    if not isinstance(value, dict) or set(value) != {"verdict", "reviewed_digest", "blockers"} or value.get("verdict") not in {"approve", "revise", "deny", "needs-human"} or not isinstance(value.get("reviewed_digest"), str) or not DIGEST.fullmatch(value["reviewed_digest"]): fail("synthesis result mismatch")
    validate_blockers(value.get("blockers")); return value

def advance(args):
    operation = args.operation_id
    if not isinstance(operation, str) or not OPERATION.fullmatch(operation): fail("invalid operation ID")
    env = {"HOME":str(args.hermes_home.parent), "HERMES_HOME":str(args.hermes_home), "PATH":os.environ.get("PATH", ""), "PYTHONUTF8":"1", "PYTHONDONTWRITEBYTECODE":"1", "HERMES_SAFE_MODE":"1"}
    command = [str(args.hermes_bin)]; listing = [*command, "kanban", "--board", BOARD, "list", "--tenant", operation, "--archived", "--json"]
    listed = run(listing, env, True)
    if not isinstance(listed, list): fail("invalid task listing")
    records, problem = {}, None
    for task in listed:
        try:
            if not isinstance(task, dict) or not isinstance(task.get("id"), str) or not task["id"]: fail("invalid listed task")
            body = decode(task.get("body"), "task body", True)
            if not isinstance(body, dict) or body.get("operation_id") != operation or body.get("role") not in ROLES or type(body.get("round")) is not int or body["round"] not in range(3): fail("task body identity mismatch")
            key = (body["round"], body["role"])
            if key in records: fail("duplicate role in round")
            records[key] = {"id":task["id"], "body":body}
        except ValueError as error: problem = problem or str(error)
    roots = [task for (round_, role), task in records.items() if role == "root" and round_ == 0]
    task_ids = sorted(task["id"] for task in records.values())
    if len(roots) != 1: fail("cannot identify exactly one root")

    def show(task_id):
        value = run([*command, "kanban", "--board", BOARD, "show", task_id, "--json"], env, True)
        if not isinstance(value, dict) or not isinstance(value.get("task"), dict): fail("invalid task show")
        return value
    def output(status, digest=None):
        value = {"status":status, "task_ids":sorted(task_ids)}
        if digest is not None: value["digest"] = digest
        return value
    def human(summary, digest=None):
        root = show(roots[0]["id"])["task"]; status = root.get("status")
        if status == "blocked":
            if root.get("assignee") is not None: run([*command, "kanban", "--board", BOARD, "assign", root["id"], "none"], env)
            run([*command, "kanban", "--board", BOARD, "unblock", root["id"]], env); root = show(root["id"])["task"]; status = root.get("status")
        if status == "ready" and root.get("assignee") is None:
            run([*command, "kanban", "--board", BOARD, "request-review", root["id"], "--summary", summary], env); status = "review"
        return output("done" if status in {"done", "archived"} else "review" if status == "review" else "waiting", digest)
    def route(reason, digest=None, verdict="needs-human", blockers=None):
        return human(canonical({"verdict":verdict, "reviewed_digest":digest, "blockers":blockers or [], "reason":reason}), digest)
    def verify(key, body, parents):
        record = records[key]; value = show(record["id"]); task = value["task"]
        current = value.get("parents")
        if task.get("id") != record["id"] or task.get("body") != canonical(body) or not isinstance(current, list) or sorted(current) != sorted(parents): fail("PRD graph edge/body conflict")
        return task
    def revision(round_):
        present = {role:records[(round_, role)]["body"] for item, role in records if item == round_}
        if not present: fail("empty revision round")
        sample = next(iter(present.values())); digest, blockers = sample.get("prior_digest"), sample.get("blockers")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest): fail("revision digest conflict")
        validate_blockers(blockers); reviewers = selected(blockers)
        bodies = {role:{"operation_id":operation, "round":round_, "role":role, "prior_digest":digest, "blockers":blockers} for role in ["writer", *reviewers, "synthesis"]}
        bodies["synthesis"]["review_roles"] = reviewers
        if any(role not in bodies or body != bodies[role] for role, body in present.items()): fail("revision body conflict")
        return digest, blockers, reviewers, bodies
    def create(round_, role, body, parents):
        args = [*command, "kanban", "--board", BOARD, "create", f"PRD round {round_}: {role}", "--body", canonical(body), "--assignee", PROFILES[role], "--idempotency-key", f"{operation}:{operation}:{round_}:{role}", "--tenant", operation, "--max-runtime", "1800", "--max-retries", "1", "--completion-contract", "local-only", "--created-by", "operator", "--initial-status", "blocked", "--json"]
        for parent in parents: args.extend(("--parent", parent))
        try: value = run(args, env, True); task_id = value.get("id") if isinstance(value, dict) else None
        except ValueError:
            adopted = [item for item in run(listing, env, True) if isinstance(item, dict) and item.get("body") == canonical(body)]
            task_id = adopted[0].get("id") if len(adopted) == 1 else None
        if not isinstance(task_id, str) or not task_id: fail("invalid task create result")
        records[(round_, role)] = {"id":task_id, "body":body}; task_ids.append(task_id)
    def release(round_, roles):
        released = False
        for role in reversed(roles):
            value = show(records[(round_, role)]["id"]); task, events, runs = value["task"], value.get("events"), value.get("runs")
            pristine = task.get("status") == "blocked" and runs == [] and isinstance(events, list) and len(events) == 2 and events[0].get("kind") == "created" and events[1].get("kind") == "blocked" and events[1].get("payload") == {"reason":"initial_status", "status":"blocked", "actor":"operator"}
            if pristine: run([*command, "kanban", "--board", BOARD, "unblock", task["id"]], env); released = True
        return released

    if problem or len([key for key in records if key[1] == "root"]) != 1: return route(problem or "root task conflict")
    root_state = show(roots[0]["id"])["task"].get("status")
    if root_state in {"review", "done", "archived", "running"}: return output("done" if root_state in {"done", "archived"} else "review" if root_state == "review" else "waiting")
    try:
        root_body = records[(0, "root")]["body"]; intake = root_body.get("intake")
        if not isinstance(intake, dict) or set(intake) != {"operation_id", "title", "requester", "requirements"} or intake.get("operation_id") != operation: fail("round-zero body conflict")
        full = {"operation_id":operation, "round":0, "intake":intake}
        bodies0 = {
            "root":{**full, "role":"root", "output":"remain blocked and unassigned; intake.json attachment is source of record"},
            "writer":{**full, "role":"writer", "output":"attach draft PRD bytes and raw-byte SHA-256 digest"},
            **{role:{"operation_id":operation, "round":0, "role":role, "draft_digest":"writer result attachment raw-byte SHA-256", "output":"attach review bound to draft digest"} for role in ("product-pm", "mvp", "occams-razor")},
            "synthesis":{"operation_id":operation, "round":0, "role":"synthesis", "draft_digest":"writer result attachment raw-byte SHA-256", "review_roles":["product-pm", "mvp", "occams-razor"], "output":"attach verdict bound to digest and reviews; do not edit PRD bytes"},
        }
        expected0 = {(0, role) for role in bodies0}
        if {key for key in records if key[0] == 0} != expected0: fail("round-zero role conflict")
        for role, body in bodies0.items():
            parents = [] if role in {"root", "writer"} else [records[(0, "writer")]["id"]] if role != "synthesis" else [records[(0, item)]["id"] for item in ("product-pm", "mvp", "occams-razor")]
            verify((0, role), body, parents)
        rounds = {round_ for round_, role in records if role != "root"}
        if not rounds or rounds != set(range(max(rounds) + 1)): fail("revision round conflict")
        healed = False
        for round_ in range(1, max(rounds) + 1):
            digest, blockers, reviewers, bodies = revision(round_); roles = ["writer", *reviewers, "synthesis"]
            parent = show(records[(round_ - 1, "synthesis")]["id"])["task"]
            prior = synthesis_result(parent.get("result"))
            if parent.get("status") != "done" or prior["verdict"] != "revise" or prior["reviewed_digest"] != digest or prior["blockers"] != blockers: fail("revision parent conflict")
            actual = {role for item, role in records if item == round_}
            if actual != set(roles):
                if round_ != max(rounds) or not actual <= set(roles): fail("revision role conflict")
                for role in roles:
                    if (round_, role) in records: continue
                    parents = [records[(round_ - 1, "synthesis")]["id"]] if role == "writer" else [records[(round_, "writer")]["id"]] if role != "synthesis" else [records[(round_, item)]["id"] for item in reviewers]
                    create(round_, role, bodies[role], parents); healed = True
            for role in roles:
                parents = [records[(round_ - 1, "synthesis")]["id"]] if role == "writer" else [records[(round_, "writer")]["id"]] if role != "synthesis" else [records[(round_, item)]["id"] for item in reviewers]
                verify((round_, role), bodies[role], parents)
        current = max(rounds)
        released = False
        if current:
            _, _, reviewers, _ = revision(current); released = release(current, ["writer", *reviewers, "synthesis"])
    except ValueError as error: return route(str(error))

    synthesis = show(records[(current, "synthesis")]["id"])["task"]
    if synthesis.get("status") != "done": return output("revision" if healed or released else "waiting")
    writer_id = records[(current, "writer")]["id"]; expected_name = "draft.md" if current == 0 else "revision.md"
    try:
        attachments = run([*command, "kanban", "--board", BOARD, "attachments", writer_id, "--json"], env, True)
        if not isinstance(attachments, list) or len(attachments) != 1: fail("writer attachment conflict")
        attachment = attachments[0]
        if not isinstance(attachment, dict) or attachment.get("filename") != expected_name or attachment.get("content_type") != "text/markdown" or type(attachment.get("size")) is not int or not 0 < attachment["size"] <= 256 * 1024: fail("writer attachment mismatch")
        result = synthesis_result(synthesis.get("result"))
    except ValueError as error: return route(str(error))
    verdict, digest, blockers = result["verdict"], result["reviewed_digest"], result["blockers"]
    open_blockers = [item for item in blockers if item["status"] == "open"]
    if verdict == "approve":
        if open_blockers: return route("approve verdict has open blockers", digest, blockers=blockers)
        summary = {"verdict":"approve", "writer_task_id":writer_id, "attachment_filename":attachment["filename"], "attachment_size":attachment["size"], "reviewed_digest":digest, "blockers":blockers}
        return human(canonical(summary), digest)
    summary = canonical({"verdict":verdict, "reviewed_digest":digest, "blockers":blockers})
    if verdict != "revise" or current == 2: return human(summary, digest)
    next_round = current + 1; reviewers = selected(blockers); roles = ["writer", *reviewers, "synthesis"]
    bodies = {role:{"operation_id":operation, "round":next_round, "role":role, "prior_digest":digest, "blockers":blockers} for role in roles}; bodies["synthesis"]["review_roles"] = reviewers
    for role in roles:
        parents = [records[(current, "synthesis")]["id"]] if role == "writer" else [records[(next_round, "writer")]["id"]] if role != "synthesis" else [records[(next_round, item)]["id"] for item in reviewers]
        create(next_round, role, bodies[role], parents)
    try:
        for role in roles:
            parents = [records[(current, "synthesis")]["id"]] if role == "writer" else [records[(next_round, "writer")]["id"]] if role != "synthesis" else [records[(next_round, item)]["id"] for item in reviewers]
            verify((next_round, role), bodies[role], parents)
    except ValueError as error: return route(str(error), digest, blockers=blockers)
    release(next_round, roles); return output("revision", digest)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--hermes-home", type=pathlib.Path, required=True); parser.add_argument("--hermes-bin", type=pathlib.Path, required=True); parser.add_argument("--operation-id", required=True)
    try: print(canonical(advance(parser.parse_args())))
    except (OSError, ValueError) as error: raise SystemExit(f"Hermes PRD advance failed: {error}")
if __name__ == "__main__": main()
