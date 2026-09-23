#!/usr/bin/env python3
"""Create restricted Kanban council profiles from existing Hermes identities."""
import argparse
import json
import os
import pwd
import shutil
from pathlib import Path

import yaml

WORKFLOW = "pr-risk-council"
TITLES = {
    "orchestrator":"Council Orchestrator", "review":"Council Reviewer", "security":"Council Security",
    "reliability":"Council Reliability", "architecture":"Council Architect", "verification":"Council Verifier",
}


def fail(message): raise ValueError(message)


def load_contract(path):
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or data.get("workflow") != WORKFLOW or data.get("engine") != "kanban":
        fail("invalid Kanban workflow contract")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or len(profiles) != 6 or set(profiles) != {
        "council-orchestrator", "council-reviewer", "council-security",
        "council-reliability", "council-architect", "council-verifier"}:
        fail("unexpected council profile set")
    if profiles["council-orchestrator"]["model"] != "claude-sonnet-5" \
            or any(value["model"] != "claude-haiku-4-5-20251001"
                   for name, value in profiles.items() if name != "council-orchestrator"):
        fail("model cost policy changed")
    if {value["role"] for value in profiles.values()} != set(TITLES): fail("role set changed")
    return data


def safe_dir(path, uid):
    if not path.is_dir() or path.is_symlink() or path.stat().st_uid != uid: fail(f"unsafe directory: {path}")


def safe_file(path, uid):
    if path.is_symlink() or not path.is_file(): fail(f"unsafe file: {path}")
    info = path.stat()
    if info.st_uid != uid or info.st_nlink != 1: fail(f"unsafe file: {path}")


def source_material(root, uid):
    safe_dir(root, uid); soul, meta, skills = root / "SOUL.md", root / "profile.yaml", root / "skills"
    safe_file(soul, uid); safe_file(meta, uid); safe_dir(skills, uid)
    skill_paths = list(skills.rglob("*"))
    if any(path.is_symlink() for path in skill_paths): fail(f"unsafe source skill tree: {root.name}")
    files = [soul, *sorted(path for path in skill_paths if path.is_file())]
    if any(path.stat().st_uid != uid or path.stat().st_nlink != 1 for path in files):
        fail(f"unsafe source skill tree: {root.name}")
    metadata = yaml.safe_load(meta.read_text()) or {}
    if not isinstance(metadata, dict) or not str(metadata.get("description") or "").strip():
        fail(f"source profile description missing: {root.name}")
    return soul, skills, str(metadata["description"]).strip()


def profile_config(role, model):
    return {
        "model":{"provider":"anthropic","default":model},
        "fallback_providers":[],
        "delegation":{"fallback_providers":[]},
        "platform_toolsets":{"cli":[],"api_server":["no_mcp"]},
        "plugins":{"enabled":[]},
        "auxiliary":{"background_review":{"enabled":False}},
        "memory":{"memory_enabled":False,"retention_enabled":False,"user_profile_enabled":False},
        "skills":{"creation_nudge_interval":0},
        "agent":{"disabled_toolsets":["delegation"],"max_turns":80,"api_max_retries":0},
    }


def state_path(home):
    return home / "workflow-backups" / "pr-risk-council-kanban-profiles.state"


def atomic_text(path, text, uid, gid):
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(text); output.flush(); os.fsync(output.fileno())
    os.chown(temporary, uid, gid); os.chmod(temporary, 0o600); os.replace(temporary, path)


def set_state(home, value, uid, gid):
    state = state_path(home)
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
        # NOTE: uid/hardlink checks are not repeated in create_profile/apply; source files are
        # validated here (prepare) but not re-validated at copy time. This gap is intentional and
        # acceptable while apply/restore are not yet exposed via CLI (board-canary PR pending).
        prepared.append((target, policy, soul, skills, description))
    return prepared


def create_profile(profile_root, item, uid, gid):
    target, policy, soul, skills, description = item
    temporary = profile_root / f".{target}.tmp-{os.getpid()}"
    temporary.mkdir(mode=0o700)
    try:
        (temporary / "SOUL.md").write_bytes(soul.read_bytes())
        shutil.copytree(skills, temporary / "skills", symlinks=False)
        (temporary / "config.yaml").write_text(yaml.safe_dump(profile_config(policy["role"], policy["model"]), sort_keys=False))
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
    prepared = prepare(home, contract, uid); state = state_path(home)
    if state.exists() or state.is_symlink(): fail("Kanban council profile state already exists; restore first")
    set_state(home, "applying", uid, gid)
    created = []
    try:
        for item in prepared:
            create_profile(home / "profiles", item, uid, gid); created.append(item[0])
        set_state(home, "applied", uid, gid)
    except Exception:
        try:
            remove_targets(home, contract)
            state.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return {"workflow":WORKFLOW,"applied":True,"profiles":created,"model_ceiling":"claude-sonnet-5"}


def restore(home, contract, uid, gid):
    state = state_path(home); safe_file(state, uid)
    if state.read_text().strip() not in {"applying", "applied", "restoring"}:
        fail("invalid Kanban council profile state")
    set_state(home, "restoring", uid, gid)
    remove_targets(home, contract)
    state.unlink()
    return {"workflow":WORKFLOW,"restored":True}


def check(home, contract, uid):
    prepared = prepare(home, contract, uid)
    return {"workflow":WORKFLOW,"ready":True,"sources":[item[1]["source"] for item in prepared],
            "targets":[item[0] for item in prepared],"model_ceiling":"claude-sonnet-5","writes":0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--contract", type=Path, required=True)
    # NOTE: --apply and --restore subcommands are intentionally absent from this CLI.
    # They will be added in the board-canary PR once effective runtime tool-policy conformance,
    # task model allowlists, token/workflow caps, and crash recovery are in place.
    args = parser.parse_args()
    try:
        user = pwd.getpwnam(args.service_user); contract = load_contract(args.contract)
        result = check(args.hermes_home, contract, user.pw_uid)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise SystemExit(f"Hermes Kanban profile configuration failed: {error}")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__": main()
