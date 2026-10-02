#!/usr/bin/env python3
"""Create or adopt one PRD round-zero graph through the supported Hermes CLI."""
import argparse, hashlib, json, os, pathlib, re, subprocess, sys, tempfile

BOARD, BOARD_NAME = "prd-write", "PRD Write"
ENGINES = ("fixed", "dynamic")
DEFAULT_REPOSITORY_CACHE = pathlib.Path("/Users/Shared/ai-pr-automation-runtime/repositories")
DEFAULT_KNOWLEDGE_REPOSITORIES = ("ROKT/ads-success-kb", "ROKT/zhach-private-docs")
ROLES = ("root", "writer", "product-pm", "mvp", "occams-razor", "synthesis")
PROFILES = {"root": None, "writer": "prd-write-v1", "product-pm": "product-pm", "mvp": "mvp", "occams-razor": "occams-razor", "synthesis": "prd-write-v1"}
DYNAMIC_WRITER_CONTRACT = {
    "writer_execution": ["run every writer in goal mode with goal_max_turns=4 and max_runtime_seconds=3600", "complete only after verified artifact, reviewer fanout, synthesis fan-in, and exact created_cards", "reviewer and synthesis tasks remain single-shot"],
    "artifact": ["write UTF-8 Markdown with write_file inside scratch workspace", "require verified=true", "compute real SHA-256 with execute_code", "declare absolute path in kanban_complete artifacts", "never use kanban_attach or a placeholder digest"],
    "fanout": ["create product-pm, mvp, and occams-razor reviewers with current writer as parent", "assign roles to matching profiles exactly: product-pm to product-pm, mvp to mvp, occams-razor to occams-razor", "create one prd-write-v1 synthesis with writer and all reviewers as parents", "use prd-write:{operation}:{round}:{role} idempotency keys", "do not set task skills; every child body must be self-contained and carry repository plus knowledge evidence contracts", "complete writer with every returned child ID in created_cards"],
    "reviewer_body": ["include workflow, operation, stage=reviewer, round, role, source filename, digest, repository snapshots, knowledge sources, and full role rubric", "read durable writer attachment from parent context with read_file", "spot-check repository and organizational-knowledge claims against the same sources and cite source provenance", "return pass|revise|needs_human|deny plus blockers, advisories, attachment identity, digest, and evidence citations", "do not create tasks"],
    "synthesis_body": ["include workflow, operation, stage=synthesis, round, reviewer roles, source filename, digest, and these complete branch rules", "dedupe blockers and record owner role", "approve: block needs_input with final attachment identity", "revise below round 2: create exactly one prd-write-v1 goal-mode writer with goal_max_turns=4, max_runtime_seconds=3600, current synthesis as parent, and a self-contained copy of this contract", "revision council always includes mvp and occams-razor plus unresolved blocker owners", "missing or malformed evidence, deny recommendation, or requested round 3: block needs_input", "on a resumed human block, validate and apply human_decision below before reviewer synthesis"],
    "human_decision": ["accept only newest comment prefixed human_decision_v1 whose author is exactly default", "require comment after latest needs_input block and before latest unblock event", "require matching operation and current attachment digest", "approve completes synthesis with metadata.status=approved", "revise requires non-empty reason and creates exactly one next writer subject to round cap", "deny requires non-empty reason and completes synthesis with metadata.status=denied", "invalid or stale decision re-blocks needs_input"],
}
DESIGN_WRITER_CONTRACT = {
    "writer_execution": DYNAMIC_WRITER_CONTRACT["writer_execution"],
    "artifact": DYNAMIC_WRITER_CONTRACT["artifact"],
    "sections": ["requirements source, current architecture, goals and non-goals", "proposed boundaries, components, APIs, events, schemas, state ownership, data and deployment flow", "security, reliability, performance, operability, migration, rollout, rollback, tests, observability, alternatives and trade-offs", "Mermaid topology or sequence diagrams where useful"],
    "fanout": ["create software-architect, mvp, and occams-razor reviewers with current writer as parent", "assign roles to matching profiles exactly: software-architect to software-architect, mvp to mvp, occams-razor to occams-razor", "create one design-write-v1 synthesis with writer and all reviewers as parents", "use design-write:{operation}:{round}:{role} idempotency keys", "do not set task skills; every child body is self-contained with repository and knowledge evidence", "complete writer with every returned child ID in created_cards"],
    "reviewer_body": ["include complete architecture, MVP, or Occam rubric", "read design attachment and spot-check claims against pinned sources", "return pass|revise|needs_human|deny with blockers, owner roles, advisories, attachment identity, digest, and evidence", "do not create tasks"],
    "synthesis_body": ["dedupe blockers and preserve owner roles", "pass blocks needs_input with final attachment identity and advisories", "revise below round 2 creates one goal-mode design-write-v1 writer with mandatory three reviewers", "missing evidence, unresolved authority, deny recommendation, or round 3 request blocks needs_input"],
    "human_decision": DYNAMIC_WRITER_CONTRACT["human_decision"],
}
ROADMAP_WRITER_CONTRACT = {
    "writer_execution": DYNAMIC_WRITER_CONTRACT["writer_execution"],
    "artifact": DYNAMIC_WRITER_CONTRACT["artifact"],
    "sections": ["planning horizon, goals, outcomes, evidence, assumptions, candidates, and exclusions", "prioritization rationale, now-next-later sequencing, dependencies, critical path, capacity, staffing, ownership, milestones, and gates", "risks, guardrails, rollback and de-scope options, metrics, measurement owner, review cadence, unresolved decisions, and Mermaid dependency or timeline diagrams"],
    "fanout": ["create product-pm, vp-eng, mvp, and occams-razor reviewers with current writer as parent", "assign roles exactly: product-pm to product-pm, vp-eng to vp-eng, mvp to mvp, occams-razor to occams-razor", "create one roadmap-write-v1 synthesis with writer and all reviewers as parents", "use roadmap-write:{operation}:{round}:{role} idempotency keys", "every child body is self-contained with evidence and no task skills", "complete writer with every returned child ID in created_cards"],
    "reviewer_body": ["include complete Product PM, VP Engineering, MVP, or Occam rubric", "spot-check outcome, dependency, effort, and capacity claims against pinned evidence", "return pass|revise|needs_human|deny with blockers, owners, advisories, attachment identity, digest, and evidence", "do not create tasks"],
    "synthesis_body": ["dedupe blockers and preserve owners", "pass blocks needs_input with attachment identity, assumptions, advisories, and unresolved decisions", "revise below round 2 creates one goal-mode roadmap-write-v1 writer with all four mandatory reviewers", "missing evidence, absent priority or capacity authority, deny recommendation, or round 3 request blocks needs_input"],
    "human_decision": DYNAMIC_WRITER_CONTRACT["human_decision"],
}
DOCUMENTS = {
    "prd": {"board": BOARD, "name": BOARD_NAME, "prefix": "prd", "profile": "prd-write-v1", "reviewers": ["product-pm", "mvp", "occams-razor"], "contract": DYNAMIC_WRITER_CONTRACT, "label": "PRD"},
    "design": {"board": "design-write", "name": "Design Write", "prefix": "design", "profile": "design-write-v1", "reviewers": ["software-architect", "mvp", "occams-razor"], "contract": DESIGN_WRITER_CONTRACT, "label": "design"},
    "roadmap": {"board": "roadmap-write", "name": "Roadmap Write", "prefix": "roadmap", "profile": "roadmap-write-v1", "reviewers": ["product-pm", "vp-eng", "mvp", "occams-razor"], "contract": ROADMAP_WRITER_CONTRACT, "label": "roadmap"},
}

def fail(message): raise ValueError(message)
def canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
def load_request(prefix="prd"):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: fail(f"duplicate request key: {key}")
            value[key] = item
        return value
    raw = getattr(sys.stdin, "buffer", sys.stdin).read(); raw = raw.encode() if isinstance(raw, str) else raw
    try: value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: fail("invalid JSON number"))
    except json.JSONDecodeError as error: raise ValueError("invalid request JSON") from error
    if not isinstance(value, dict) or set(value) not in ({"operation_id", "title", "requester", "requirements"}, {"operation_id", "title", "requester", "requirements", "repositories"}): fail("request keys differ from contract")
    for key, limit in (("title", 256), ("requester", 200), ("requirements", 65536)):
        if not isinstance(value[key], str) or not value[key] or len(value[key]) > limit: fail(f"invalid {key}")
    repositories = value.get("repositories", [])
    if not isinstance(repositories, list) or len(repositories) > 20 or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", item) for item in repositories) or len(set(repositories)) != len(repositories): fail("invalid repositories")
    if raw != canonical(value): fail("intake must be canonical JSON")
    core = {key: value[key] for key in ("title", "requester", "requirements")}
    if "repositories" in value: core["repositories"] = repositories
    if value["operation_id"] != prefix + "-" + hashlib.sha256(canonical(core)).hexdigest(): fail("operation ID differs from canonical request")
    return value, raw

def run(command, env, json_output=False):
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    if completed.returncode: fail(f"Hermes CLI failed: {completed.stderr.strip()[-300:]}")
    if not json_output: return completed.stdout.strip()
    try: return json.loads(completed.stdout)
    except json.JSONDecodeError as error: raise ValueError("Hermes CLI returned invalid JSON") from error

def enqueue(args):
    document = DOCUMENTS[args.document_kind]; board_slug, board_name = document["board"], document["name"]
    request, intake = load_request(document["prefix"]); operation = request["operation_id"]
    env = {"HOME": str(args.hermes_home.parent), "HERMES_HOME": str(args.hermes_home), "PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "HERMES_SAFE_MODE": "1"}; command = [str(args.hermes_bin)]
    boards = run([*command, "kanban", "boards", "list", "--all", "--json"], env, True)
    board = next((item for item in boards if item.get("slug") == board_slug), None)
    if board is None: run([*command, "kanban", "boards", "create", board_slug, "--name", board_name], env)
    elif board.get("name") != board_name: fail("board name mismatch")
    existing = run([*command, "kanban", "--board", board_slug, "list", "--tenant", operation, "--json"], env, True)
    contracts = set()
    for task in existing:
        try: body = json.loads(task.get("body", ""))
        except (AttributeError, json.JSONDecodeError): fail("existing operation has unknown task contract")
        if body.get("workflow") == board_slug and body.get("operation") == operation: contracts.add("dynamic")
        elif body.get("operation_id") == operation: contracts.add("fixed")
        else: fail("existing operation has unknown task contract")
    if contracts and contracts != {args.engine}: fail("operation already belongs to other workflow engine")
    if args.engine == "dynamic":
        repositories = request.get("repositories", [])
        knowledge = list(args.knowledge_repositories)
        repository_rules = [
            "Before drafting, use execute_code to manage only intake.repositories and knowledge_sources in $HERMES_HOME/repository-cache with /usr/local/libexec/ai-pr-automation/hermes-repository-cache.",
            "Check each requested repository with /usr/local/libexec/ai-pr-automation/hermes-authority.py --check before enrollment; never take a remote, ref, credential, or path from intake.",
            "For missing repositories enroll; for stale manifests sync; then materialize pinned snapshots. Set GIT_ASKPASS=/usr/local/libexec/ai-pr-automation/hermes-git-read-askpass, GIT_ASKPASS_REQUIRE=force, GIT_TERMINAL_PROMPT=0, GITHUB_READ_TOKEN_FILE=/Users/Shared/ai-pr-automation-runtime/secrets/github-read-token; never print credentials.",
            "Verify repository identity, snapshot_sha == head_sha, the exact versioned snapshot path, and fetched_at age <= 3600 seconds before reading; block if any source is unavailable or unsafe.",
            "Inspect every pinned snapshot, cite current-state claims as OWNER/REPO@SHA:path:line, and include the pinned repository and knowledge evidence in each reviewer body and writer result.",
        ]
        body = canonical({
            "workflow": board_slug,
            "operation": operation,
            "stage": "writer",
            "round": 0,
            "role": "writer",
            "intake": request,
            "reviewer_roles": document["reviewers"],
            "repositories": repositories,
            "knowledge_sources": knowledge,
            "repository_rules": repository_rules,
            "knowledge_rules": ["search ads-success-kb and private-docs snapshots for relevant decisions, plans, incidents, ownership, and prior rejected approaches", "recall memory-ads-success and memory-org but never retain", "query DocShare, RoktGPT, Atlassian, and Buildkite when relevant", "cite snapshot paths, document IDs or URLs, memory IDs, issue keys, and build references", "summarize only necessary non-sensitive evidence; never dump raw private or recalled content", "surface conflicts between code, docs, tickets, and memory"],
            "contract": document["contract"],
            "output": f"write verified {document['label']} artifact, create required reviewers and synthesis, then complete with exact created_cards",
        }).decode()
        create = [*command, "kanban", "--board", board_slug, "create", request["title"], "--body", body, "--idempotency-key", f"{board_slug}:{operation}:0:writer", "--tenant", operation, "--max-runtime", "3600", "--max-retries", "1", "--goal", "--goal-max-turns", "4", "--completion-contract", "local-only", "--created-by", "operator", "--initial-status", "blocked", "--assignee", document["profile"], "--json"]
        created = run(create, env, True)
        task_id = created.get("id") if isinstance(created, dict) else None
        status = created.get("status") if isinstance(created, dict) else None
        if not isinstance(task_id, str) or not task_id or not isinstance(status, str): fail("invalid task create result")
        if created.get("body") != body or created.get("assignee") != document["profile"] or created.get("tenant") != operation or created.get("parents") not in ([], None) or created.get("skills") not in ([], None): fail("dynamic writer task mismatch")
        attachments = run([*command, "kanban", "--board", board_slug, "attachments", task_id, "--json"], env, True)
        if not attachments:
            with tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / "intake.json"; path.write_bytes(intake)
                run([*command, "kanban", "--board", board_slug, "attach", task_id, str(path), "--name", "intake.json", "--content-type", "application/json", "--author", "operator"], env)
        elif len(attachments) != 1 or attachments[0].get("filename") != "intake.json" or attachments[0].get("size") != len(intake) or attachments[0].get("content_type") != "application/json": fail("writer attachment mismatch")
        if status == "blocked":
            shown = run([*command, "kanban", "--board", board_slug, "show", task_id, "--json"], env, True)
            events, runs = shown.get("events"), shown.get("runs")
            initial = isinstance(events, list) and runs == [] and len(events) == 3 and events[0].get("kind") == "created" \
                and events[1].get("kind") == "blocked" and events[1].get("payload") == {"reason":"initial_status","status":"blocked","actor":"operator"} \
                and events[2].get("kind") == "attached" and events[2].get("payload", {}).get("filename") == "intake.json"
            if initial:
                run([*command, "kanban", "--board", board_slug, "unblock", task_id], env)
                if run([*command, "kanban", "--board", board_slug, "show", task_id, "--json"], env, True).get("task", {}).get("status") == "blocked": fail("task remained blocked")
        return {"board": board_slug, "operation_id": operation, "tasks": {"writer": task_id}}
    if args.document_kind != "prd": fail("fixed engine is available only for PRD recovery")
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
    parser = argparse.ArgumentParser(); parser.add_argument("--hermes-home", type=pathlib.Path, required=True); parser.add_argument("--hermes-bin", type=pathlib.Path, required=True); parser.add_argument("--engine", choices=ENGINES, default="fixed"); parser.add_argument("--document-kind", choices=DOCUMENTS, default="prd"); parser.add_argument("--repository-cache-root", type=pathlib.Path, default=DEFAULT_REPOSITORY_CACHE, help="legacy option; writers manage repository evidence"); parser.add_argument("--knowledge-repository", action="append", dest="knowledge_repositories", default=list(DEFAULT_KNOWLEDGE_REPOSITORIES))
    try: print(json.dumps(enqueue(parser.parse_args()), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError) as error: raise SystemExit(f"Hermes document enqueue failed: {error}")
if __name__ == "__main__": main()
