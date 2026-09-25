#!/usr/bin/env python3
"""Create restricted Kanban council profiles from existing Hermes identities."""
import argparse
import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path

import yaml

WORKFLOW = "pr-risk-council"
COUNCIL_TOOLS_COMMAND = "/usr/local/libexec/ai-pr-automation/hermes-council-tools"
COUNCIL_TOOLS = [
    "snapshot_read", "snapshot_search", "kanban_show", "kanban_comment",
    "kanban_heartbeat", "kanban_complete", "kanban_block",
]
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
TITLES = {
    "orchestrator":"Council Orchestrator", "synthesis":"Council Synthesizer",
    "review":"Council Reviewer", "security":"Council Security", "reliability":"Council Reliability",
    "architecture":"Council Architect", "verification":"Council Verifier",
}
V1_PROFILES = {
    "council-orchestrator", "council-reviewer", "council-security",
    "council-reliability", "council-architect", "council-verifier",
}
V2_PROFILES = {
    "council-orchestrator-v2", "council-reviewer-v2", "council-security-v2",
    "council-reliability-v2", "council-architect-v2",
}
V2_ROLES = ["review", "security", "reliability", "architecture"]


def fail(message): raise ValueError(message)


def load_contract(path):
    data = json.loads(path.read_text())
    version = data.get("schema_version")
    if version not in {1, 2} or data.get("workflow") != WORKFLOW or data.get("engine") != "kanban":
        fail("invalid Kanban workflow contract")
    profiles = data.get("profiles")
    expected = V1_PROFILES if version == 1 else V2_PROFILES
    if not isinstance(profiles, dict) or set(profiles) != expected:
        fail("unexpected council profile set")
    orchestrator = "council-orchestrator" if version == 1 else "council-orchestrator-v2"
    synthesis_role = "orchestrator" if version == 1 else "synthesis"
    if profiles[orchestrator] != {"source":"orchestrator","role":synthesis_role,"model":"claude-sonnet-5"} \
            or any(value.get("model") != "claude-haiku-4-5-20251001"
                   for name, value in profiles.items() if name != orchestrator):
        fail("model cost policy changed")
    expected_roles = set(TITLES) - ({"synthesis"} if version == 1 else {"orchestrator", "verification"})
    if {value.get("role") for value in profiles.values()} != expected_roles:
        fail("role set changed")
    if version == 2:
        if set(data) != {"schema_version", "workflow", "engine", "board", "deadline_seconds",
                         "token_budget", "max_active_workflows", "profiles", "task_graph", "tools"} \
                or data.get("tools") != COUNCIL_TOOLS:
            fail("invalid v2 Kanban workflow contract")
        expected_sources = {"council-reviewer-v2":"reviewer", "council-security-v2":"security-engineer",
                            "council-reliability-v2":"site-reliability-engineer",
                            "council-architect-v2":"technical-architect", "council-orchestrator-v2":"orchestrator"}
        if any(profiles[name].get("source") != source for name, source in expected_sources.items()):
            fail("v2 source profile changed")
        expected_graph = {"specialists":V2_ROLES,"synthesis":"synthesis",
                          "edges":[[role, "synthesis"] for role in V2_ROLES]}
        if data.get("board") != WORKFLOW or data.get("deadline_seconds") != 900 \
                or data.get("token_budget") != 150000 or data.get("max_active_workflows") != 1 \
                or data.get("task_graph") != expected_graph:
            fail("v2 workflow contract changed")
    return data


def safe_dir(path, uid):
    if not path.is_dir() or path.is_symlink() or path.stat().st_uid != uid: fail(f"unsafe directory: {path}")


def safe_file(path, uid):
    if path.is_symlink() or not path.is_file(): fail(f"unsafe file: {path}")
    info = path.stat()
    if info.st_uid != uid or info.st_nlink != 1: fail(f"unsafe file: {path}")


def validate_shared_skill(link, trusted_root, uid):
    try: resolved, trusted_root = link.resolve(strict=True), trusted_root.resolve(strict=True)
    except OSError: fail(f"broken shared skill link: {link}")
    if trusted_root not in resolved.parents or not resolved.is_dir() or resolved.is_symlink():
        fail(f"shared skill link escaped trusted root: {link}")
    for path in (resolved, *resolved.rglob("*")):
        info = path.lstat()
        if path.is_symlink() or info.st_uid not in {0, uid} or (path.is_file() and info.st_nlink != 1):
            fail(f"unsafe shared skill tree: {link.name}")


def source_material(root, uid):
    safe_dir(root, uid); soul, meta, skills = root / "SOUL.md", root / "profile.yaml", root / "skills"
    safe_file(soul, uid); safe_file(meta, uid); safe_dir(skills, uid)
    trusted_root = root.parents[1].parent / "hermes-profiles" / "skills"
    skill_paths = list(skills.rglob("*"))
    for path in skill_paths:
        if path.is_symlink(): validate_shared_skill(path, trusted_root, uid)
        else:
            info = path.stat()
            if info.st_uid != uid or (path.is_file() and info.st_nlink != 1):
                fail(f"unsafe source skill tree: {root.name}")
    metadata = yaml.safe_load(meta.read_text()) or {}
    if not isinstance(metadata, dict) or not str(metadata.get("description") or "").strip():
        fail(f"source profile description missing: {root.name}")
    return soul, skills, str(metadata["description"]).strip()


def profile_config(role, model, version=1):
    config = {
        "model":{"provider":"anthropic","default":model},
        "fallback_providers":[],
        "delegation":{"fallback_providers":[]},
        "platform_toolsets":{"cli":["council-tools"] if version == 2 else [],"api_server":["no_mcp"]},
        "plugins":{"enabled":[]},
        "auxiliary":{"background_review":{"enabled":False}},
        "memory":{"memory_enabled":False,"retention_enabled":False,"user_profile_enabled":False},
        "skills":{"creation_nudge_interval":0},
        "agent":{"disabled_toolsets":["delegation", "kanban"] if version == 2 else ["delegation"],
                 "max_turns":80,"api_max_retries":0},
    }
    if version == 2:
        config["mcp_servers"] = {"council-tools":{"command":COUNCIL_TOOLS_COMMAND,"args":[],"env":COUNCIL_ENV,
                                                   "enabled":True,"tools":{"include":COUNCIL_TOOLS}}}
    return config


def state_path(home, contract=None):
    suffix = "-v2" if contract and contract["schema_version"] == 2 else ""
    return home / "workflow-backups" / f"pr-risk-council-kanban{suffix}-profiles.state"


def atomic_text(path, text, uid, gid):
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(text); output.flush(); os.fsync(output.fileno())
    os.chown(temporary, uid, gid); os.chmod(temporary, 0o600); os.replace(temporary, path)


def configure_worker_env(home, contract, snapshot_root, workflow_root, python, uid, gid):
    if contract["schema_version"] != 2:
        fail("worker runtime environment requires v2 profiles")
    values = {"PR_SAFETY_SNAPSHOT_ROOT":str(snapshot_root),
              "PR_SAFETY_WORKFLOW_ROOT":str(workflow_root),
              "HERMES_COUNCIL_TOOLS_PYTHON":str(python)}
    for name in contract["profiles"]:
        profile = home / "profiles" / name
        safe_dir(profile, uid); validate_target(profile)
        path = profile / ".env"
        if path.exists() or path.is_symlink(): safe_file(path, uid)
        lines = path.read_text().splitlines() if path.exists() else []
        lines = [line for line in lines if line.split("=", 1)[0] not in values]
        lines.extend(f"{key}={json.dumps(value)}" for key, value in values.items())
        atomic_text(path, "\n".join(lines) + "\n", uid, gid)


def set_state(home, contract, value, uid, gid):
    state = state_path(home, contract)
    if state.parent.exists(): safe_dir(state.parent, uid)
    else: state.parent.mkdir(mode=0o700, parents=True); os.chown(state.parent, uid, gid)
    os.chmod(state.parent, 0o700)
    atomic_text(state, value + "\n", uid, gid)


def prepare(home, contract, uid):
    profile_root = home / "profiles"; safe_dir(profile_root, uid); prepared = []
    for target, policy in contract["profiles"].items():
        destination = profile_root / target
        if destination.exists() or destination.is_symlink(): fail(f"target profile already exists: {target}")
        source = profile_root / policy["source"]
        soul, skills, description = source_material(source, uid)
        prepared.append((target, policy, soul, skills, description, contract["schema_version"]))
    return prepared


def create_profile(profile_root, item, uid, gid):
    target, policy, soul, skills, description, version = item
    _, _, current_description = source_material(soul.parent, uid)
    if current_description != description: fail(f"source profile changed during apply: {policy['source']}")
    temporary = profile_root / f".{target}.tmp-{os.getpid()}"
    temporary.mkdir(mode=0o700)
    try:
        (temporary / "SOUL.md").write_bytes(soul.read_bytes())
        shutil.copytree(skills, temporary / "skills", symlinks=False)
        expected_config = profile_config(policy["role"], policy["model"], version)
        (temporary / "config.yaml").write_text(yaml.safe_dump(expected_config, sort_keys=False))
        meta = {"description":description,"display_name":TITLES[policy["role"]],
                "workflow":{"name":WORKFLOW,"source_profile":policy["source"]}}
        (temporary / "profile.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
        (temporary / ".no-bundled-skills").write_text("")
        (temporary / ".council-profile").write_text(WORKFLOW + "\n")
        for path in temporary.rglob("*"):
            if path.is_file(): os.chmod(path, 0o600)
        os.chown(temporary, uid, gid)
        for path in temporary.rglob("*"): os.chown(path, uid, gid)
        os.replace(temporary, profile_root / target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True); raise


def validate_target(path):
    if not path.exists(): return
    marker = path / ".council-profile"
    if path.is_symlink() or not path.is_dir() or marker.is_symlink() or not marker.is_file() \
            or marker.read_text().strip() != WORKFLOW:
        fail(f"unsafe council profile target: {path.name}")


def remove_targets(home, contract):
    targets = [home / "profiles" / name for name in contract["profiles"]]
    for target in targets: validate_target(target)
    for target in targets:
        if target.exists(): shutil.rmtree(target)
    if any(target.exists() for target in targets): fail("council profile cleanup incomplete")


def apply(home, contract, uid, gid):
    prepared = prepare(home, contract, uid); state = state_path(home, contract)
    if state.exists() or state.is_symlink(): fail("Kanban council profile state already exists; restore first")
    set_state(home, contract, "applying", uid, gid)
    created = []
    try:
        for item in prepared:
            create_profile(home / "profiles", item, uid, gid); created.append(item[0])
        set_state(home, contract, "applied", uid, gid)
    except Exception:
        try:
            remove_targets(home, contract)
            state.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return {"workflow":WORKFLOW,"applied":True,"profiles":created,"model_ceiling":"claude-sonnet-5"}


def restore(home, contract, uid, gid):
    state = state_path(home, contract); safe_file(state, uid)
    if state.read_text().strip() not in {"applying", "applied", "restoring"}:
        fail("invalid Kanban council profile state")
    set_state(home, contract, "restoring", uid, gid)
    remove_targets(home, contract)
    state.unlink()
    return {"workflow":WORKFLOW,"restored":True}


def check(home, contract, uid):
    prepared = prepare(home, contract, uid)
    result = {"workflow":WORKFLOW,"ready":True,"sources":[item[1]["source"] for item in prepared],
              "targets":[item[0] for item in prepared],"model_ceiling":"claude-sonnet-5","writes":0}
    if contract["schema_version"] == 2:
        result.update({"profiles":contract["profiles"],"tools":COUNCIL_TOOLS})
    return result


def require_stopped():
    labels = ("com.example.ai-pr-automation-hermes", "com.example.ai-pr-automation-hermes-dashboard",
              "com.example.ai-pr-automation-hermes-kanban-safety-bridge")
    for label in labels:
        try: loaded = subprocess.run(["launchctl", "print", f"system/{label}"], capture_output=True).returncode == 0
        except FileNotFoundError: loaded = False
        if loaded: fail("stop Hermes gateway, dashboard, and safety bridge before apply/restore")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--contract", type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    try:
        user = pwd.getpwnam(args.service_user); contract = load_contract(args.contract)
        if (args.apply or args.restore) and os.geteuid() != user.pw_uid:
            fail("apply/restore must run as service user")
        if args.apply or args.restore: require_stopped()
        result = restore(args.hermes_home, contract, user.pw_uid, user.pw_gid) if args.restore else \
                 apply(args.hermes_home, contract, user.pw_uid, user.pw_gid) if args.apply else \
                 check(args.hermes_home, contract, user.pw_uid)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise SystemExit(f"Hermes Kanban profile configuration failed: {error}")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__": main()
