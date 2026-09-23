#!/usr/bin/env python3
"""Read-only feasibility check for a Kanban-backed PR Risk Council."""
import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
MODEL_RE = re.compile(r"^claude-(?:sonnet-5|haiku-4-5-20251001)$")
EXPECTED_TOP = {"schema_version", "workflow", "engine", "board", "deadline_seconds", "token_budget",
                "max_active_workflows", "profiles", "task_graph"}
PROFILE_FIELDS = {"source", "role", "model"}
REQUIRED_MODULES = (
    "hermes_cli/kanban.py", "hermes_cli/kanban_boards.py", "hermes_cli/kanban_db.py",
    "hermes_cli/kanban_db_graph.py", "hermes_cli/kanban_db_dispatch.py",
    "hermes_cli/kanban_pr_acceptance.py", "gateway/kanban_watchers_dispatcher.py",
    "tools/kanban_tools.py", "tools/kanban_tools_schemas.py",
)
REQUIRED_TOOL_NAMES = (
    "kanban_show", "kanban_complete", "kanban_request_review", "kanban_request_changes",
    "kanban_block", "kanban_heartbeat", "kanban_comment", "kanban_create", "kanban_link",
)


def fail(message):
    raise ValueError(message)


def load_contract(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) != EXPECTED_TOP or data["schema_version"] != 1 \
            or data["workflow"] != "pr-risk-council" or data["engine"] != "kanban" \
            or data["board"] != "pr-risk-council":
        fail("invalid workflow contract")
    if (data["deadline_seconds"], data["token_budget"], data["max_active_workflows"]) != (900, 150000, 1):
        fail("workflow budget changed")
    profiles = data["profiles"]
    if not isinstance(profiles, dict) or len(profiles) != 6:
        fail("workflow requires six profiles")
    sources, roles = set(), set()
    for name, value in profiles.items():
        if not NAME_RE.fullmatch(name) or not isinstance(value, dict) or set(value) != PROFILE_FIELDS:
            fail(f"invalid profile contract: {name}")
        if not NAME_RE.fullmatch(value["source"]) or not NAME_RE.fullmatch(value["role"]) \
                or not MODEL_RE.fullmatch(value["model"]):
            fail(f"invalid source, role, or model: {name}")
        sources.add(value["source"]); roles.add(value["role"])
    if len(sources) != 6 or profiles["council-orchestrator"] != {
            "source":"orchestrator","role":"orchestrator","model":"claude-sonnet-5"}:
        fail("orchestrator contract changed")
    if any(value["model"] != "claude-haiku-4-5-20251001"
           for name, value in profiles.items() if name != "council-orchestrator"):
        fail("specialist model contract changed")
    graph = data["task_graph"]
    specialists = ["review", "security", "reliability", "architecture"]
    edges = [[role, "verification"] for role in specialists]
    if not isinstance(graph, dict) or set(graph) != {"specialists", "verifier", "edges"} \
            or graph["specialists"] != specialists or graph["verifier"] != "verification" \
            or graph["edges"] != edges or set([*specialists, "verification", "orchestrator"]) != roles:
        fail("task graph roles or edges changed")
    return data


def safe_file(path, uid):
    if path.is_symlink() or not path.is_file(): fail(f"required file missing or unsafe: {path}")
    info = path.stat()
    if info.st_uid != uid or info.st_nlink != 1: fail(f"required file has unsafe ownership: {path}")


def profile_report(home, contract):
    uid, result = os.geteuid(), {}
    for target, value in contract["profiles"].items():
        root = home / "profiles" / value["source"]
        if not root.is_dir() or root.is_symlink() or root.stat().st_uid != uid:
            fail(f"source profile missing or unsafe: {value['source']}")
        config_path, meta_path = root / "config.yaml", root / "profile.yaml"
        safe_file(config_path, uid); safe_file(meta_path, uid)
        config, meta = yaml.safe_load(config_path.read_text()) or {}, yaml.safe_load(meta_path.read_text()) or {}
        if not isinstance(config, dict) or not isinstance(meta, dict) or not str(meta.get("description") or "").strip():
            fail(f"source profile metadata invalid: {value['source']}")
        result[target] = {"source":value["source"],"role":value["role"],"model":value["model"],
                          "description":str(meta["description"]).strip()}
    return result


def runtime_report(home, install, profiles):
    for relative in REQUIRED_MODULES:
        path = install / relative
        if not path.is_file() or path.is_symlink(): fail(f"Kanban runtime module missing or unsafe: {relative}")
    catalog = install / "hermes_cli/models_catalog_static.py"
    defaults = install / "hermes_cli/config_defaults.py"
    parser = install / "hermes_cli/kanban_parser.py"
    schemas = install / "tools/kanban_tools_schemas.py"
    for path in (catalog, defaults, parser, schemas):
        if not path.is_file() or path.is_symlink(): fail(f"runtime contract file missing or unsafe: {path.name}")
    model_ids = set(re.findall(r'["\']([^"\']+)["\']', catalog.read_text()))
    if any(value["model"] not in model_ids for value in profiles.values()):
        fail("workflow model missing from pinned catalog")
    parser_source, schema_source = parser.read_text(), schemas.read_text()
    if "--model" not in parser_source or "--provider" not in parser_source: fail("per-task model override support missing")
    if any(name not in schema_source for name in REQUIRED_TOOL_NAMES): fail("required Kanban tool missing")
    python = install / "venv/bin/python"
    if not python.is_file(): fail("pinned Hermes Python missing")
    probe = r'''import json,sys
from pathlib import Path
from hermes_cli.config_defaults import DEFAULT_CONFIG
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
keys=['dispatch_in_gateway','review_dispatch','dispatch_interval_seconds','failure_limit','max_in_progress','max_in_progress_per_profile','auto_decompose','dispatch_stale_timeout_seconds','reconcile_orphans']
conn=kbc.connect(Path(sys.argv[1]))
roles=['reviewer','security-engineer','site-reliability-engineer','technical-architect']
parents=[kb.create_task(conn,title='fixture-'+role,assignee=role) for role in roles]
verifier=kb.create_task(conn,title='fixture-verifier',assignee='verifier',parents=parents)
for task,role in zip(parents,roles):
 kb.add_comment(conn,task,'orchestrator','artifact_digest=fixture')
 assert kb.complete_task(conn,task,summary='complete',metadata={'role':role,'artifact_digest':'fixture'},fire_lifecycle_hook=False)
assert kb.get_task(conn,verifier).status=='ready'
review=kb.create_task(conn,title='fixture-review',assignee='reviewer')
run=kb.claim_task(conn,review,claimer='fixture-implementer')
assert kb.request_review(conn,review,summary='ready',reviewer='verifier',expected_run_id=run.current_run_id)
review_run=kb.claim_review_task(conn,review,claimer='fixture-reviewer')
assert review_run is not None
assert kb.complete_task(conn,review,summary='approved',metadata={'artifact_digest':'fixture'},expected_run_id=review_run.current_run_id,fire_lifecycle_hook=False)
print(json.dumps({'defaults':{x:DEFAULT_CONFIG['kanban'].get(x) for x in keys},'graph':{'parents':len(parents),'verifier_status':kb.get_task(conn,verifier).status},'review_status':kb.get_task(conn,review).status,'comment_count':len(kb.list_comments(conn,parents[0]))},sort_keys=True))
conn.close()
'''
    env = {"PATH":os.environ.get("PATH", ""),"PYTHONPATH":str(install),"PYTHONUTF8":"1",
           "PYTHONDONTWRITEBYTECODE":"1","HERMES_HOME":str(home)}
    with tempfile.TemporaryDirectory(prefix="hermes-kanban-preflight-") as temporary:
        completed = subprocess.run([python, "-B", "-c", probe, str(Path(temporary) / "kanban.db")],
                                   capture_output=True, text=True, cwd=install, env=env, timeout=30)
    if completed.returncode: fail(f"Kanban isolated probe failed: {completed.stderr.strip()[:300]}")
    values = json.loads(completed.stdout)
    required = {"dispatch_in_gateway":True,"review_dispatch":True,"dispatch_interval_seconds":60,
                "failure_limit":2,"max_in_progress":None,"max_in_progress_per_profile":None,
                "auto_decompose":True,"dispatch_stale_timeout_seconds":14400,"reconcile_orphans":True}
    if values.get("defaults") != required:
        fail("Kanban runtime defaults changed")
    if values.get("graph") != {"parents":4,"verifier_status":"ready"} \
            or values.get("review_status") != "done" or values.get("comment_count") != 1:
        fail("Kanban isolated graph/review probe failed")
    return values


def preflight(home, install, contract_path):
    contract = load_contract(contract_path)
    profiles = profile_report(home, contract)
    runtime = runtime_report(home, install, profiles)
    return {"schema_version":1,"workflow":contract["workflow"],"engine":"kanban","feasible":True,
            "board":contract["board"],"profiles":profiles,"kanban_defaults":runtime["defaults"],
            "isolated_probe":{"graph":runtime["graph"],"review_status":runtime["review_status"],
                              "comment_count":runtime["comment_count"]},
            "required_overrides":{"auto_decompose":False,"max_in_progress":5,
                                  "max_in_progress_per_profile":1},
            "deferred_runtime_enforcement":["deadline_seconds","max_active_workflows","profile_tool_policy","token_budget"],
            "required_tools":list(REQUIRED_TOOL_NAMES),"service_state_writes":0,"model_calls":0,
            "isolated_temporary_database":True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args()
    try: report = preflight(args.hermes_home, args.install_dir, args.contract)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"Hermes Kanban workflow preflight failed: {error}")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__": main()
