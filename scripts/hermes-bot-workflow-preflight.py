#!/usr/bin/env python3
"""Read-only feasibility check for the pinned Hermes PR Risk Council workflow."""
import argparse
import ast
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
MODEL_RE = re.compile(r"^claude-(?:sonnet-4-6|haiku-4-5-20251001)$")
REQUIRED_METHODS = {"groups.capabilities", "groups.create", "groups.state", "groups.send",
                    "groups.log", "groups.stop", "groups.retry", "groups.approve"}


def fail(message):
    raise ValueError(message)


def contract(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) != {"schema_version", "workflow", "profiles"} \
            or data["schema_version"] != 1 or data["workflow"] != "pr-risk-council":
        fail("invalid workflow contract")
    profiles = data["profiles"]
    if not isinstance(profiles, dict) or not 2 <= len(profiles) <= 6:
        fail("workflow needs two to six profiles")
    for name, value in profiles.items():
        if not PROFILE_RE.fullmatch(name) or not isinstance(value, dict) or set(value) != {"title", "model"} \
                or not isinstance(value["title"], str) or not MODEL_RE.fullmatch(value["model"]):
            fail(f"invalid profile contract: {name}")
    if profiles.get("orchestrator", {}).get("model") != "claude-sonnet-4-6" \
            or any(value["model"] != "claude-haiku-4-5-20251001"
                   for name, value in profiles.items() if name != "orchestrator"):
        fail("model cost policy changed")
    return data


def assignment(path, name):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                return ast.literal_eval(node.value)
    fail(f"runtime constant missing: {name}")


def regular(path, uid, required=True):
    if path.is_symlink(): fail(f"unsafe path: {path}")
    if not path.exists():
        if required: fail(f"required path missing: {path}")
        return False
    info = path.stat()
    if not path.is_file() or info.st_uid != uid or info.st_nlink != 1: fail(f"unsafe path: {path}")
    return True


def projected_profiles(home, policy):
    result = []
    uid = os.geteuid()
    for name, expected in policy["profiles"].items():
        root = home / "profiles" / name
        if not root.is_dir() or root.is_symlink() or root.stat().st_uid != uid:
            fail(f"profile missing or unsafe: {name}")
        config_path = root / "config.yaml"
        regular(config_path, uid)
        if (root / "mcp.json").exists() or (root / "mcp.json").is_symlink():
            fail(f"profile configures MCP: {name}")
        config = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(config, dict): fail(f"profile config is not a mapping: {name}")
        model = config.setdefault("model", {}); agent = config.setdefault("agent", {})
        platforms = config.setdefault("platform_toolsets", {}); delegation = config.setdefault("delegation", {})
        if not all(isinstance(value, dict) for value in (model, agent, platforms, delegation)):
            fail(f"profile config sections are invalid: {name}")
        model.update(provider="anthropic", default=expected["model"])
        config["fallback_providers"] = []; delegation["fallback_providers"] = []
        agent["bot_mode_protocol"] = True; platforms["api_server"] = ["no_mcp"]
        result.append({"profile":name,"config":config,"model":expected["model"]})
    return result


def runtime_report(install, projected):
    discussion = install / "gateway/hosted_room_discussion.py"
    methods_file = install / "tui_gateway/methods_groups.py"
    policy_file = install / "gateway/hosted_room_execution_policy.py"
    dm_file = install / "tools/bot_mode_dm.py"
    catalog = install / "hermes_cli/models_catalog_static.py"
    for path in (discussion, methods_file, policy_file, dm_file, catalog):
        if not path.is_file() or path.is_symlink(): fail(f"pinned runtime file missing or unsafe: {path}")
    limits = {name: assignment(discussion, name) for name in
              ("MIN_DISCUSSION_MEMBERS", "MAX_DISCUSSION_MEMBERS", "MAX_DISCUSSION_ROUNDS", "MAX_DISCUSSION_MESSAGES")}
    if limits != {"MIN_DISCUSSION_MEMBERS":2,"MAX_DISCUSSION_MEMBERS":6,
                  "MAX_DISCUSSION_ROUNDS":3,"MAX_DISCUSSION_MESSAGES":10}:
        fail("hosted discussion limits changed")
    methods = set(assignment(methods_file, "_METHODS"))
    if not REQUIRED_METHODS <= methods: fail("hosted group methods missing")
    dm_source, models = dm_file.read_text(), catalog.read_text()
    if "MESSAGE_AGENT_TOOL_NAME" not in dm_source: fail("message_agent support missing")
    if any(item["model"] not in models for item in projected): fail("configured model missing from pinned catalog")
    python = install / "venv/bin/python"
    if not python.is_file(): fail("pinned Hermes Python missing")
    probe = ("import json,sys; from gateway.hosted_room_execution_policy import execution_policy_mapping; "
             "items=json.load(sys.stdin); print(json.dumps([execution_policy_mapping(target_profile=i['profile'],config=i['config']) for i in items]))")
    env = {"PATH":os.environ.get("PATH", ""),"PYTHONPATH":str(install),"PYTHONUTF8":"1"}
    completed = subprocess.run([python, "-c", probe], input=json.dumps(projected), capture_output=True,
                               text=True, cwd=install, env=env, timeout=30)
    if completed.returncode: fail(f"hosted policy probe failed: {completed.stderr.strip()[:300]}")
    policies = json.loads(completed.stdout)
    if any(item.get("enabled_toolsets") != ["bot_room"] for item in policies):
        fail("hosted policy exposes tools beyond bot_room")
    return {"limits":limits,"methods":sorted(REQUIRED_METHODS),"policies":policies}


def preflight(home, install, contract_path):
    policy = contract(contract_path)
    projected = projected_profiles(home, policy)
    runtime = runtime_report(install, projected)
    return {"schema_version":1,"workflow":policy["workflow"],"ready":True,
            "profile_count":len(projected),"models":{item["profile"]:item["model"] for item in projected},
            "hosted_limits":runtime["limits"],"hosted_methods":runtime["methods"],
            "toolsets":{item["target_profile"]:item["enabled_toolsets"] for item in runtime["policies"]},
            "writes":0,"model_calls":0,"messages":0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try: report = preflight(args.hermes_home, args.install_dir, args.contract)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"Hermes workflow preflight failed: {error}")
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    if args.output: args.output.write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__": main()
