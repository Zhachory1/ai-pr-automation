#!/usr/bin/env python3
"""Run one sanitized, effect-free PR Risk Council graph on Hermes Kanban."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

BOARD = "pr-risk-council"
WORKFLOW_ID = "pr-risk-council-fixture-v1"
SPECIALISTS = {
    "review":"council-reviewer", "security":"council-security",
    "reliability":"council-reliability", "architecture":"council-architect",
}
VERIFIER = "council-verifier"
MODELS = {**{role:"claude-haiku-4-5-20251001" for role in SPECIALISTS},
          "verification":"claude-haiku-4-5-20251001"}
TERMINAL = {"done", "blocked", "archived"}
FIXTURE = {
    "title":"Sanitized tenant deletion guard change",
    "intent":"Delete expired records for one tenant only.",
    "diff":"The changed delete query removes the tenant_id predicate and retains only expires_at < now().",
    "constraints":["No repository or network access.","Use only supplied evidence.","Do not perform external effects."],
}
ARTIFACT_DIGEST = hashlib.sha256(json.dumps(FIXTURE, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fail(message): raise ValueError(message)


def atomic_json(path, data):
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(data, output, sort_keys=True, separators=(",", ":")); output.write("\n")
        output.flush(); os.fsync(output.fileno())
    os.replace(temporary, path)


def modules(install):
    sys.path.insert(0, str(install))
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    return kb, kbc


def state_path(home): return home / "workflow-runs" / "pr-risk-council.json"


def profile_check(home):
    import yaml
    expected = {**SPECIALISTS, "verification":VERIFIER}
    for role, name in expected.items():
        root = home / "profiles" / name
        if not root.is_dir() or root.is_symlink() or (root / "mcp.json").exists(): fail(f"restricted profile unavailable: {name}")
        config = yaml.safe_load((root / "config.yaml").read_text()) or {}
        if config.get("model") != {"provider":"anthropic","default":MODELS[role]} \
                or config.get("fallback_providers") != [] \
                or (config.get("delegation") or {}).get("fallback_providers") != [] \
                or (config.get("platform_toolsets") or {}).get("cli") != [] \
                or (config.get("platform_toolsets") or {}).get("api_server") != ["no_mcp"] \
                or (config.get("plugins") or {}).get("enabled") != [] \
                or (config.get("auxiliary") or {}).get("background_review", {}).get("enabled") is not False \
                or config.get("memory") != {"memory_enabled":False,"retention_enabled":False,"user_profile_enabled":False} \
                or (config.get("agent") or {}).get("api_max_retries") != 0 \
                or "delegation" not in (config.get("agent") or {}).get("disabled_toolsets", []):
            fail(f"restricted profile policy mismatch: {name}")


def load_state(home):
    path = state_path(home)
    if not path.is_file() or path.is_symlink(): fail("council state missing or unsafe")
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or data.get("workflow_id") != WORKFLOW_ID \
            or data.get("board") != BOARD or data.get("artifact_digest") != ARTIFACT_DIGEST:
        fail("council state identity mismatch")
    return data


def find_task(conn, key):
    row = conn.execute("SELECT id FROM tasks WHERE idempotency_key = ?", (key,)).fetchone()
    return None if row is None else str(row["id"])


def specialist_body(role):
    return json.dumps({"workflow_id":WORKFLOW_ID,"artifact_digest":ARTIFACT_DIGEST,"role":role,
        "fixture":FIXTURE,"goal":f"Assess only the {role} dimension.",
        "acceptance":["call kanban_show","add one progress comment","complete with required metadata"],
        "completion_metadata":{"workflow_id":WORKFLOW_ID,"artifact_digest":ARTIFACT_DIGEST,"role":role,
            "verdict":"clear|findings|needs_human_decision|inconclusive","claims":[],"evidence":[],
            "confidence":"low|medium|high","dissent":[],"residual_risk":[],"external_effects":0}}, sort_keys=True)


def verifier_body():
    return json.dumps({"workflow_id":WORKFLOW_ID,"artifact_digest":ARTIFACT_DIGEST,"role":"verification",
        "fixture":FIXTURE,"goal":"Read every parent handoff, preserve dissent, and synthesize one decision package.",
        "acceptance":["call kanban_show","validate all four parent handoffs and artifact digests",
                      "add one progress comment","complete with final package metadata"],
        "completion_metadata":{"workflow_id":WORKFLOW_ID,"artifact_digest":ARTIFACT_DIGEST,
            "verdict":"approve|changes_requested|needs_human_decision|inconclusive","material_findings":[],
            "consensus":[],"dissent":[],"evidence":[],"members_completed":[],"members_failed":[],
            "external_effects":0}}, sort_keys=True)


def setup(home, install):
    kb, kbc = modules(install); profile_check(home); state = state_path(home); resumed = state.exists()
    if resumed:
        seed = load_state(home)
        if seed.get("phase") not in {"setting_up", "active"}: fail("council setup state is not resumable")
    else:
        if kb.board_exists(BOARD): fail("council board exists without state")
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        seed = {"schema_version":1,"phase":"setting_up","workflow_id":WORKFLOW_ID,"board":BOARD,
                "artifact_digest":ARTIFACT_DIGEST,"tasks":{},"created_at":int(time.time())}
        atomic_json(state, seed)
    if not kb.board_exists(BOARD):
        kb.create_board(BOARD, name="PR Risk Council", description="Read-only sanitized multi-agent council")
    with kbc.connect_closing(board=BOARD) as conn:
        tasks = {}
        for role, profile in SPECIALISTS.items():
            key = f"{WORKFLOW_ID}:{role}"; task_id = find_task(conn, key)
            tasks[role] = task_id or kb.create_task(conn, title=f"Council {role} review", body=specialist_body(role),
                assignee=profile, created_by="operator", workspace_kind="scratch", idempotency_key=key,
                max_runtime_seconds=600, max_retries=0, model_override=MODELS[role],
                provider_override="anthropic", goal_mode=False, completion_contract="local-only", board=BOARD)
        key = f"{WORKFLOW_ID}:verification"; task_id = find_task(conn, key)
        tasks["verification"] = task_id or kb.create_task(conn, title="Council verify and synthesize",
            body=verifier_body(), assignee=VERIFIER, created_by="operator", workspace_kind="scratch",
            parents=list(tasks.values()), idempotency_key=key, max_runtime_seconds=600, max_retries=0,
            model_override=MODELS["verification"], provider_override="anthropic", goal_mode=False,
            completion_contract="local-only", board=BOARD)
    seed.update(phase="active", tasks=tasks); atomic_json(state, seed)
    return {"action":"setup", **seed, "resumed":resumed}


def valid_metadata(metadata, role):
    base = isinstance(metadata, dict) and metadata.get("workflow_id") == WORKFLOW_ID \
        and metadata.get("artifact_digest") == ARTIFACT_DIGEST and metadata.get("external_effects") == 0
    if not base: return False
    if role != "verification":
        return (metadata.get("role") == role and metadata.get("verdict") in {"clear","findings","needs_human_decision","inconclusive"}
                and metadata.get("confidence") in {"low","medium","high"}
                and all(isinstance(metadata.get(key), list) for key in ("claims","evidence","dissent","residual_risk")))
    return (metadata.get("verdict") in {"approve","changes_requested","needs_human_decision","inconclusive"}
            and all(isinstance(metadata.get(key), list) for key in
                    ("material_findings","consensus","dissent","evidence","members_completed","members_failed"))
            and set(metadata["members_completed"]) == set(SPECIALISTS)
            and metadata["members_failed"] == [])


def task_evidence(kb, conn, task_id, role, profile):
    task = kb.get_task(conn, task_id); runs = kb.list_runs(conn, task_id)
    comments, attachments = kb.list_comments(conn, task_id), kb.list_attachments(conn, task_id)
    completed = [run for run in runs if run.outcome == "completed"]
    metadata = completed[-1].metadata if completed else None
    verified = (task is not None and task.status == "done" and len(runs) == 1 and len(completed) == 1
                and completed[0].profile == profile and any(comment.author == profile for comment in comments)
                and not attachments and valid_metadata(metadata, role))
    return {"status":None if task is None else task.status,"attempts":len(runs),"comments":len(comments),
            "attachments":len(attachments),"verified":verified,"metadata":metadata}


def status(home, install):
    kb, kbc = modules(install); saved = load_state(home)
    if not kb.board_exists(BOARD): fail("council board missing")
    evidence = {}; expected = {**SPECIALISTS, "verification":VERIFIER}
    with kbc.connect_closing(board=BOARD) as conn:
        task_count = int(conn.execute("SELECT COUNT(*) n FROM tasks").fetchone()["n"])
        for role, profile in expected.items(): evidence[role] = task_evidence(kb, conn, saved["tasks"].get(role), role, profile)
    verified = task_count == 5 and all(item["verified"] for item in evidence.values())
    terminal = all(item["status"] in TERMINAL for item in evidence.values())
    return {"action":"status","board":BOARD,"workflow_id":WORKFLOW_ID,"artifact_digest":ARTIFACT_DIGEST,
            "task_count":task_count,"tasks":evidence,"terminal":terminal,"verified":verified}


def cleanup(home, install):
    kb, _ = modules(install); current = status(home, install)
    if current["task_count"] != 5: fail("council board task count mismatch")
    if not current["terminal"]: fail("cannot clean up active council")
    removed = kb.remove_board(BOARD, archive=True); state_path(home).unlink()
    return {"action":"cleanup","board":BOARD,"archived":removed.get("action") == "archived",
            "verified":current["verified"]}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("action", choices=("setup","status","cleanup"))
    parser.add_argument("--hermes-home", type=Path, required=True); parser.add_argument("--install-dir", type=Path, required=True)
    args = parser.parse_args()
    try: result = globals()[args.action](args.hermes_home, args.install_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error: raise SystemExit(f"Hermes PR Risk Council failed: {error}")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__": main()
