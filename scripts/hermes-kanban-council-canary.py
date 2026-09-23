#!/usr/bin/env python3
"""Create, verify, and archive one isolated Kanban council worker canary."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

BOARD = "pr-risk-council-canary"
TASK_KEY = "pr-risk-council-canary-v1"
MODEL = "claude-haiku-4-5-20251001"
PROFILE = "council-reviewer"
TERMINAL = {"done", "blocked", "archived"}


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


def state_path(home): return home / "workflow-runs" / "pr-risk-council-canary.json"


def load_state(home):
    path = state_path(home)
    if not path.is_file() or path.is_symlink(): fail("canary state missing or unsafe")
    saved = json.loads(path.read_text())
    if set(saved) != {"schema_version","phase","board","task_id","idempotency_key","profile","model","created_at"} \
            or saved["schema_version"] != 1 or saved["board"] != BOARD or saved["idempotency_key"] != TASK_KEY \
            or saved["profile"] != PROFILE or saved["model"] != MODEL:
        fail("canary state identity mismatch")
    return saved


def profile_check(home):
    root = home / "profiles" / PROFILE
    if not root.is_dir() or root.is_symlink() or (root / "mcp.json").exists(): fail("restricted canary profile unavailable")
    import yaml
    config = yaml.safe_load((root / "config.yaml").read_text()) or {}
    if config.get("model") != {"provider":"anthropic","default":MODEL} \
            or config.get("fallback_providers") != [] \
            or (config.get("delegation") or {}).get("fallback_providers") != [] \
            or (config.get("platform_toolsets") or {}).get("cli") != [] \
            or (config.get("platform_toolsets") or {}).get("api_server") != ["no_mcp"] \
            or (config.get("plugins") or {}).get("enabled") != [] \
            or (config.get("auxiliary") or {}).get("background_review", {}).get("enabled") is not False:
        fail("restricted canary profile policy mismatch")


def find_task(conn):
    row = conn.execute("SELECT id FROM tasks WHERE idempotency_key = ?", (TASK_KEY,)).fetchone()
    return None if row is None else str(row["id"])


def setup(home, install):
    kb, kbc = modules(install); profile_check(home); state = state_path(home)
    if state.exists():
        saved = load_state(home)
        if saved["phase"] not in {"setting_up", "active"}: fail("canary setup state is not resumable")
    else:
        if kb.board_exists(BOARD): fail("canary board exists without state")
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        saved = {"schema_version":1,"phase":"setting_up","board":BOARD,"task_id":None,
                 "idempotency_key":TASK_KEY,"profile":PROFILE,"model":MODEL,"created_at":int(time.time())}
        atomic_json(state, saved)
    if not kb.board_exists(BOARD):
        kb.create_board(BOARD, name="PR Risk Council Canary", description="Read-only synthetic council worker canary")
    with kbc.connect_closing(board=BOARD) as conn:
        task_id = find_task(conn)
        if task_id is None:
            body = json.dumps({
                "workflow_id":TASK_KEY,"artifact_digest":"0" * 64,"role":"review",
                "goal":"Prove task-scoped Kanban lifecycle without external effects.",
                "acceptance":["read this task with kanban_show","add one progress comment",
                              "complete with metadata containing workflow_id, artifact_digest, and external_effects=0"],
                "forbidden_effects":["attachments","child tasks","terminal","file","network","GitHub","memory","MCP"],
                "deadline_seconds":300,
            }, sort_keys=True)
            task_id = kb.create_task(conn, title="Synthetic read-only reviewer canary", body=body,
                assignee=PROFILE, created_by="operator", workspace_kind="scratch", idempotency_key=TASK_KEY,
                max_runtime_seconds=300, max_retries=0, model_override=MODEL, provider_override="anthropic",
                goal_mode=False, completion_contract="local-only", board=BOARD)
    saved.update(phase="active", task_id=task_id); atomic_json(state, saved)
    return {"action":"setup","board":BOARD,"task_id":task_id,"status":"ready"}


def status(home, install):
    kb, kbc = modules(install); saved = load_state(home)
    if not kb.board_exists(BOARD): fail("canary board missing")
    with kbc.connect_closing(board=BOARD) as conn:
        task = kb.get_task(conn, saved["task_id"])
        if task is None: fail("canary task missing")
        events, runs = kb.list_events(conn, saved["task_id"]), kb.list_runs(conn, saved["task_id"])
        comments, attachments = kb.list_comments(conn, saved["task_id"]), kb.list_attachments(conn, saved["task_id"])
        task_count = int(conn.execute("SELECT COUNT(*) n FROM tasks").fetchone()["n"])
    completed = [run for run in runs if run.outcome == "completed"]
    metadata = completed[-1].metadata if completed else None
    verified = (task.status == "done" and task_count == 1 and len(runs) == 1 and len(completed) == 1
                and completed[0].profile == PROFILE and any(comment.author == PROFILE for comment in comments)
                and not attachments
                and isinstance(metadata, dict) and metadata.get("workflow_id") == TASK_KEY
                and metadata.get("artifact_digest") == "0" * 64 and metadata.get("external_effects") == 0)
    return {"action":"status","board":BOARD,"task_id":saved["task_id"],"status":task.status,
            "assignee":task.assignee,"model":getattr(task,"model_override",None),
            "events":[event.kind for event in events],"comments":len(comments),"attachments":len(attachments),
            "attempts":len(runs),"task_count":task_count,"terminal":task.status in TERMINAL,"verified":verified}


def cleanup(home, install):
    kb, _ = modules(install); current = status(home, install)
    if current["status"] == "running": fail("cannot clean up running canary")
    if current["task_count"] != 1: fail("canary board contains unexpected tasks")
    if current["status"] == "done" and not current["verified"]: fail("done canary failed verification")
    removed = kb.remove_board(BOARD, archive=True)
    state_path(home).unlink()
    return {"action":"cleanup","board":BOARD,"archived":removed.get("action") == "archived",
            "final_status":current["status"],"verified":current["verified"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("setup", "status", "cleanup"))
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    args = parser.parse_args()
    try: result = globals()[args.action](args.hermes_home, args.install_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"Hermes Kanban council canary failed: {error}")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__": main()
