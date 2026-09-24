#!/usr/bin/env python3
"""Read-only feasibility check for a Kanban-backed PR Risk Council."""
import argparse
import ast
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

# Workflow/profile identifiers are operator-facing names; reject ambiguous one-character aliases.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
MODEL_RE = re.compile(r"^claude-(?:sonnet-5|haiku-4-5-20251001)$")
EXPECTED_TOP = {"schema_version", "workflow", "engine", "board", "deadline_seconds", "token_budget",
                "max_active_workflows", "profiles", "task_graph"}
PROFILE_FIELDS = {"source", "role", "model"}
V2_PROFILES = {
    "council-reviewer-v2":("reviewer", "review", "claude-haiku-4-5-20251001"),
    "council-security-v2":("security-engineer", "security", "claude-haiku-4-5-20251001"),
    "council-reliability-v2":("site-reliability-engineer", "reliability", "claude-haiku-4-5-20251001"),
    "council-architect-v2":("technical-architect", "architecture", "claude-haiku-4-5-20251001"),
    "council-orchestrator-v2":("orchestrator", "synthesis", "claude-sonnet-5"),
}
COUNCIL_TOOLS_COMMAND = Path("/usr/local/libexec/ai-pr-automation/hermes-council-tools")
COUNCIL_TOOLS = (
    "snapshot_read", "snapshot_search", "kanban_show", "kanban_comment",
    "kanban_heartbeat", "kanban_complete", "kanban_block",
)
MCP_MODEL_TOOLS = tuple(f"mcp__council_tools__{name}" for name in COUNCIL_TOOLS)
COUNCIL_ENV = {
    "COUNCIL_TASK_ID":"${HERMES_KANBAN_TASK}",
    "COUNCIL_RUN_ID":"${HERMES_KANBAN_RUN_ID}",
    "COUNCIL_CLAIM_LOCK":"${HERMES_KANBAN_CLAIM_LOCK}",
    "COUNCIL_BOARD":"${HERMES_KANBAN_BOARD}",
    "COUNCIL_DB":"${HERMES_KANBAN_DB}",
    "COUNCIL_WORKSPACE":"${HERMES_KANBAN_WORKSPACE}",
    "COUNCIL_SNAPSHOT_ROOT":"${PR_SAFETY_SNAPSHOT_ROOT}",
    "COUNCIL_WORKFLOW_ROOT":"${PR_SAFETY_WORKFLOW_ROOT}",
    "COUNCIL_PROFILE":"${HERMES_PROFILE}",
    "COUNCIL_TOOLS_PYTHON":"${HERMES_COUNCIL_TOOLS_PYTHON}",
}
EXPECTED_INPUT_SCHEMAS = {
    "snapshot_read":{"type":"object","properties":{"path":{"type":"string","minLength":1,"maxLength":4096}},
                     "required":["path"],"additionalProperties":False},
    "snapshot_search":{"type":"object","properties":{
        "path":{"type":"string","minLength":1,"maxLength":4096},
        "query":{"type":"string","minLength":1,"maxLength":256},
        "max_results":{"type":"integer","minimum":1,"maximum":100}},
        "required":["path","query"],"additionalProperties":False},
    "kanban_show":{"type":"object","properties":{},"required":[],"additionalProperties":False},
    "kanban_comment":{"type":"object","properties":{
        "body":{"type":"string","minLength":1,"maxLength":16000}},
        "required":["body"],"additionalProperties":False},
    "kanban_heartbeat":{"type":"object","properties":{
        "note":{"type":"string","minLength":1,"maxLength":2000}},
        "required":[],"additionalProperties":False},
    "kanban_complete":{"type":"object","properties":{
        "summary":{"type":"string","minLength":1,"maxLength":16000},
        "metadata":{"type":"object","maxProperties":64,"additionalProperties":True,
                    "description":"Bounded structured handoff metadata. The artifacts key is forbidden."}},
        "required":["summary","metadata"],"additionalProperties":False},
    "kanban_block":{"type":"object","properties":{
        "reason":{"type":"string","minLength":1,"maxLength":16000},
        "kind":{"type":"string","enum":["dependency","needs_input","capability","transient"]}},
        "required":["reason","kind"],"additionalProperties":False},
}
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
    version = data.get("schema_version") if isinstance(data, dict) else None
    expected_top = EXPECTED_TOP | ({"tools"} if version == 2 else set())
    if not isinstance(data, dict) or set(data) != expected_top or version not in {1, 2} \
            or data["workflow"] != "pr-risk-council" or data["engine"] != "kanban" \
            or data["board"] != "pr-risk-council" \
            or version == 2 and data.get("tools") != list(COUNCIL_TOOLS):
        fail("invalid workflow contract")
    if (data["deadline_seconds"], data["token_budget"], data["max_active_workflows"]) != (900, 150000, 1):
        fail("workflow budget changed")
    profiles = data["profiles"]
    expected_count = 6 if data["schema_version"] == 1 else 5
    if not isinstance(profiles, dict) or len(profiles) != expected_count:
        fail(f"workflow requires {expected_count} profiles")
    sources, roles = set(), set()
    for name, value in profiles.items():
        if not NAME_RE.fullmatch(name) or not isinstance(value, dict) or set(value) != PROFILE_FIELDS:
            fail(f"invalid profile contract: {name}")
        if not NAME_RE.fullmatch(value["source"]) or not NAME_RE.fullmatch(value["role"]) \
                or not MODEL_RE.fullmatch(value["model"]):
            fail(f"invalid source, role, or model: {name}")
        sources.add(value["source"]); roles.add(value["role"])
    specialists = ["review", "security", "reliability", "architecture"]
    graph = data["task_graph"]
    if data["schema_version"] == 1:
        if len(sources) != 6 or profiles["council-orchestrator"] != {
                "source":"orchestrator","role":"orchestrator","model":"claude-sonnet-5"}:
            fail("orchestrator contract changed")
        if any(value["model"] != "claude-haiku-4-5-20251001"
               for name, value in profiles.items() if name != "council-orchestrator"):
            fail("specialist model contract changed")
        edges = [[role, "verification"] for role in specialists]
        if not isinstance(graph, dict) or set(graph) != {"specialists", "verifier", "edges"} \
                or graph["specialists"] != specialists or graph["verifier"] != "verification" \
                or graph["edges"] != edges or set([*specialists, "verification", "orchestrator"]) != roles:
            fail("task graph roles or edges changed")
    else:
        expected = {name:{"source":source,"role":role,"model":model}
                    for name, (source, role, model) in V2_PROFILES.items()}
        expected_graph = {"specialists":specialists,"synthesis":"synthesis",
                          "edges":[[role, "synthesis"] for role in specialists]}
        if profiles != expected:
            fail("v2 source, role, or model contract changed")
        if graph != expected_graph or roles != set([*specialists, "synthesis"]):
            fail("v2 task graph roles or edges changed")
    return data


def safe_file(path, uid):
    if path.is_symlink() or not path.is_file(): fail(f"required file missing or unsafe: {path}")
    info = path.stat()
    if info.st_uid != uid or info.st_nlink != 1: fail(f"required file has unsafe ownership: {path}")


def v2_config(model):
    return {
        "model":{"provider":"anthropic","default":model},
        "fallback_providers":[],
        "delegation":{"fallback_providers":[]},
        "platform_toolsets":{"cli":["council-tools"],"api_server":["no_mcp"]},
        "plugins":{"enabled":[]},
        "auxiliary":{"background_review":{"enabled":False}},
        "memory":{"memory_enabled":False,"retention_enabled":False,"user_profile_enabled":False},
        "skills":{"creation_nudge_interval":0},
        "agent":{"disabled_toolsets":["delegation", "kanban"],"max_turns":80,"api_max_retries":0},
        "mcp_servers":{"council-tools":{"command":str(COUNCIL_TOOLS_COMMAND),"args":[],"env":COUNCIL_ENV,
                                         "enabled":True,"tools":{"include":list(COUNCIL_TOOLS)}}},
    }


def profile_report(home, contract):
    uid, result = os.geteuid(), {}
    version = contract["schema_version"]
    for target, value in contract["profiles"].items():
        root = home / "profiles" / (value["source"] if version == 1 else target)
        label = value["source"] if version == 1 else target
        if not root.is_dir() or root.is_symlink() or root.stat().st_uid != uid:
            fail(f"source profile missing or unsafe: {label}")
        config_path, meta_path = root / "config.yaml", root / "profile.yaml"
        safe_file(config_path, uid); safe_file(meta_path, uid)
        config, meta = yaml.safe_load(config_path.read_text()) or {}, yaml.safe_load(meta_path.read_text()) or {}
        if not isinstance(config, dict) or not isinstance(meta, dict) or not str(meta.get("description") or "").strip():
            fail(f"source profile metadata invalid: {label}")
        if version == 2:
            if config != v2_config(value["model"]):
                fail(f"dangerous runtime profile tools or policy drift: {target}")
            workflow = meta.get("workflow") or {}
            if workflow != {"name":"pr-risk-council","source_profile":value["source"]}:
                fail(f"source profile binding drift: {target}")
        result[target] = {"source":value["source"],"role":value["role"],"model":value["model"],
                          "description":str(meta["description"]).strip()}
    return result


def validate_installed_council_tools(path, expected_uid=0, trusted_root=Path("/")):
    path, trusted_root = Path(path), Path(trusted_root)
    try:
        relative = path.relative_to(trusted_root)
    except ValueError:
        fail("council tools escaped trusted install root")
    current = trusted_root
    root_info = current.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode) \
            or root_info.st_uid not in {0, expected_uid} or stat.S_IMODE(root_info.st_mode) & 0o022:
        fail(f"council tools has unsafe parent: {current}")
    for part in relative.parts[:-1]:
        current /= part
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) \
                or info.st_uid not in {0, expected_uid} or stat.S_IMODE(info.st_mode) & 0o022:
            fail(f"council tools has unsafe parent: {current}")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != expected_uid \
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o555:
        fail("council tools missing or unsafe")
    # In a repository checkout, compare installed bytes to the source binary. The
    # installed preflight lives beside support files under /usr/local/libexec, where
    # no repository-relative bin/ tree exists; native sync-support already performs
    # and preflights the source-to-installed byte comparison before this check runs.
    source = Path(__file__).resolve().parents[1] / "bin/hermes-council-tools"
    if source.exists() and (source.is_symlink() or not source.is_file()
            or path.read_bytes() != source.read_bytes()):
        fail("installed council tools differs from repository source")


def council_tools_report(path=COUNCIL_TOOLS_COMMAND, expected_uid=0, trusted_root=Path("/")):
    validate_installed_council_tools(path, expected_uid, trusted_root)
    requests = "\n".join((
        json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}),
        json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}),
    )) + "\n"
    with tempfile.TemporaryDirectory(prefix="hermes-council-tools-protocol-") as temporary:
        root = Path(temporary); db = root / "kanban.db"; db.touch()
        workspace = root / "workspace"; workspace.mkdir()
        snapshots = root / "snapshots"; snapshots.mkdir()
        workflows = root / "workflows"; workflows.mkdir()
        env = {"PATH":os.environ.get("PATH", ""),"PYTHONDONTWRITEBYTECODE":"1",
               "COUNCIL_TASK_ID":"preflight-dummy","COUNCIL_RUN_ID":"1",
               "COUNCIL_CLAIM_LOCK":"preflight-lock","COUNCIL_BOARD":"preflight-board",
               "COUNCIL_DB":str(db),"COUNCIL_WORKSPACE":str(workspace),
               "COUNCIL_SNAPSHOT_ROOT":str(snapshots),"COUNCIL_WORKFLOW_ROOT":str(workflows),
               "COUNCIL_PROFILE":"council-reviewer-v2","COUNCIL_TOOLS_PYTHON":sys.executable}
        completed = subprocess.run([str(path)], input=requests, capture_output=True, text=True, timeout=5, env=env)
    if completed.returncode:
        fail("council tools protocol probe failed")
    messages = [json.loads(line) for line in completed.stdout.splitlines()]
    if len(messages) != 2 \
            or messages[0].get("result", {}).get("serverInfo", {}).get("name") != "hermes-council-tools":
        fail("council tools initialization drift")
    tools = messages[1].get("result", {}).get("tools")
    if not isinstance(tools, list) or tuple(tool.get("name") for tool in tools) != COUNCIL_TOOLS:
        fail("council tools tool set drift")
    for tool in tools:
        if set(tool) != {"name", "description", "inputSchema"} \
                or not isinstance(tool["description"], str) or not tool["description"] \
                or tool["inputSchema"] != EXPECTED_INPUT_SCHEMAS[tool["name"]]:
            fail("council tools canonical schema drift")
    definitions = [{"type":"function","function":{
        "name":f"mcp__council_tools__{tool['name']}",
        "description":tool["description"],"parameters":tool["inputSchema"]}} for tool in tools]
    return {"command":str(COUNCIL_TOOLS_COMMAND),"installed_path":str(path),
            "tools":list(COUNCIL_TOOLS),"definitions":definitions}


def effective_tool_report(home, install, profiles, expected_definitions, council_tools):
    probe = r'''import json,sys
from hermes_cli.config import load_config,read_raw_config
from hermes_cli.tools_config import _get_platform_tools
from tools.mcp_tool_discovery import discover_mcp_tools
from tools import kanban_tools
from model_tools import get_tool_definitions
handlers=['_handle_show','_handle_comment','_handle_heartbeat','_handle_complete','_handle_block']
if not all(callable(getattr(kanban_tools,name,None)) for name in handlers):
 raise RuntimeError('pinned Kanban handlers changed')
expected_config=json.loads(sys.argv[1])
expected_definitions=json.loads(sys.argv[2])
raw=read_raw_config()
if raw != expected_config:
 raise RuntimeError('effective profile config differs from pinned config')
config=load_config()
if config.get('model') != expected_config['model'] or config.get('fallback_providers') != [] or (config.get('delegation') or {}).get('fallback_providers') != []:
 raise RuntimeError('effective model or fallback changed')
enabled=sorted(_get_platform_tools(config,'cli',include_default_mcp_servers=False))
if enabled != ['council-tools']:
 raise RuntimeError('resolved CLI toolsets changed: '+repr(enabled))
discovered=sorted(discover_mcp_tools(allowed_mcp_names=['council-tools']))
expected_mcp=sorted(item['function']['name'] for item in expected_definitions)
if discovered != expected_mcp:
 raise RuntimeError('council-tools MCP discovery changed: '+repr(discovered))
definitions=get_tool_definitions(enabled_toolsets=enabled,
 disabled_toolsets=(config.get('agent') or {}).get('disabled_toolsets'),quiet_mode=True,
 skip_tool_search_assembly=True)
definitions=sorted(definitions,key=lambda item:item['function']['name'])
expected_definitions=sorted(expected_definitions,key=lambda item:item['function']['name'])
names=[item['function']['name'] for item in definitions]
builtins=[name for name in names if not name.startswith('mcp__')]
if builtins:
 raise RuntimeError('built-in worker tools exposed: '+','.join(builtins))
if definitions != expected_definitions:
 raise RuntimeError('effective worker tool schemas changed: '+repr(definitions))
print(json.dumps({'resolved_toolsets':enabled,'model_tool_names':names,
 'model':config.get('model'),'fallback_providers':config.get('fallback_providers'),
 'delegation_fallback_providers':(config.get('delegation') or {}).get('fallback_providers')},sort_keys=True))
'''
    python = install / "venv/bin/python"
    result = {}
    encoded_definitions = json.dumps(expected_definitions, sort_keys=True, separators=(",", ":"))
    with tempfile.TemporaryDirectory(prefix="hermes-profile-tool-probe-") as temporary:
        probe_root = Path(temporary)
        for target, profile in profiles.items():
            source_home = home / "profiles" / target
            if any(path.is_symlink() for path in source_home.rglob("*")):
                fail(f"effective worker tool probe profile contains symlink: {target}")
            profile_home = probe_root / target
            shutil.copytree(source_home, profile_home)
            expected_config = v2_config(profile["model"])
            runtime = probe_root / f"runtime-{target}"; runtime.mkdir()
            db = runtime / "kanban.db"; db.touch()
            workspace = runtime / "workspace"; workspace.mkdir()
            snapshots = runtime / "snapshots"; snapshots.mkdir()
            workflows = runtime / "workflows"; workflows.mkdir()
            env = {"PATH":os.environ.get("PATH", ""),"PYTHONPATH":str(install),"PYTHONUTF8":"1",
                   "PYTHONDONTWRITEBYTECODE":"1","HERMES_HOME":str(profile_home),
                   "HERMES_KANBAN_TASK":"preflight-dummy","HERMES_KANBAN_RUN_ID":"1",
                   "HERMES_KANBAN_CLAIM_LOCK":"preflight-lock","HERMES_KANBAN_BOARD":"preflight-board",
                   "HERMES_KANBAN_DB":str(db),"HERMES_KANBAN_WORKSPACE":str(workspace),
                   "PR_SAFETY_SNAPSHOT_ROOT":str(snapshots),"PR_SAFETY_WORKFLOW_ROOT":str(workflows),
                   "HERMES_PROFILE":target,"HERMES_COUNCIL_TOOLS_PYTHON":str(python),
                   "COUNCIL_TOOLS_PROBE_COMMAND":str(council_tools)}
            completed = subprocess.run([python, "-B", "-c", probe,
                                        json.dumps(expected_config, sort_keys=True, separators=(",", ":")),
                                        encoded_definitions], capture_output=True, text=True,
                                       cwd=install, env=env, timeout=30)
            if completed.returncode:
                fail(f"effective worker tool probe failed for {target}: {completed.stderr.strip()[-500:]}")
            values = json.loads(completed.stdout)
            if values.get("model_tool_names") != sorted(MCP_MODEL_TOOLS) \
                    or values.get("resolved_toolsets") != ["council-tools"] \
                    or values.get("model") != expected_config["model"] \
                    or values.get("fallback_providers") != [] \
                    or values.get("delegation_fallback_providers") != []:
                fail(f"effective worker tool probe returned invalid evidence for {target}")
            result[target] = {"toolsets":["council-tools"],"tools":list(COUNCIL_TOOLS),
                              "model":values["model"],"fallback_providers":[]}
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
    model_tree = ast.parse(catalog.read_text(), filename=str(catalog))
    model_ids = {node.value for node in ast.walk(model_tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    missing_models = sorted({value["model"] for value in profiles.values()} - model_ids)
    if missing_models:
        fail(f"workflow model missing from pinned catalog: {', '.join(missing_models)}")
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
stop_helpers=['_dispatch_tick_lock','_terminate_reclaimed_worker','write_txn']
if not callable(getattr(kbc,stop_helpers[0],None)) or any(not callable(getattr(kb,name,None)) for name in stop_helpers[1:]):
 raise RuntimeError('pinned Kanban stop helpers changed')
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
print(json.dumps({'defaults':{x:DEFAULT_CONFIG['kanban'].get(x) for x in keys},'graph':{'parents':len(parents),'verifier_status':kb.get_task(conn,verifier).status},'review_status':kb.get_task(conn,review).status,'comment_count':len(kb.list_comments(conn,parents[0])),'stop_safety_helpers':stop_helpers},sort_keys=True))
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
    if values.get("stop_safety_helpers") != ["_dispatch_tick_lock","_terminate_reclaimed_worker","write_txn"]:
        fail("pinned Kanban stop helpers changed")
    return values


def preflight(home, install, contract_path, council_tools=COUNCIL_TOOLS_COMMAND,
              council_tools_uid=0, council_tools_trusted_root=Path("/")):
    contract = load_contract(contract_path)
    profiles = profile_report(home, contract)
    runtime = runtime_report(home, install, profiles)
    version = contract["schema_version"]
    report = {"schema_version":version,"workflow":contract["workflow"],"engine":"kanban","feasible":True,
              "board":contract["board"],"profiles":profiles,"kanban_defaults":runtime["defaults"],
              "isolated_probe":{"graph":runtime["graph"],"review_status":runtime["review_status"],
                                "comment_count":runtime["comment_count"]},
              "stop_safety_helpers":runtime["stop_safety_helpers"],
              "required_overrides":{"auto_decompose":False,"max_in_progress":5,
                                    "max_in_progress_per_profile":1},
              "deferred_runtime_enforcement":["deadline_seconds","max_active_workflows","profile_tool_policy","token_budget"],
              "required_tools":list(REQUIRED_TOOL_NAMES),"service_state_writes":0,"model_calls":0,
              "isolated_temporary_database":True}
    if version == 2:
        report["council_tools"] = council_tools_report(
            Path(council_tools), council_tools_uid, council_tools_trusted_root)
        report["effective_worker_tools"] = effective_tool_report(
            home, install, profiles, report["council_tools"]["definitions"], Path(council_tools))
        report["required_tools"] = list(COUNCIL_TOOLS)
        report["deferred_runtime_enforcement"] = ["deadline_seconds", "max_active_workflows", "token_budget"]
    return report


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
