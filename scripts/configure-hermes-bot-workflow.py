#!/usr/bin/env python3
"""Safely configure existing Hermes profiles for a bounded Bot Mode workflow."""
import argparse
import base64
import json
import os
import pwd
import re
from pathlib import Path

import yaml

PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
MODEL_RE = re.compile(r"^claude-(?:sonnet-5|haiku-4-5-20251001)$")
EXPECTED = {"schema_version", "workflow", "profiles"}
PROFILE_FIELDS = {"title", "model"}


def fail(message):
    raise ValueError(message)


def load_contract(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) != EXPECTED or data["schema_version"] != 1 \
            or data["workflow"] != "pr-risk-council":
        fail("invalid workflow contract")
    profiles = data["profiles"]
    if not isinstance(profiles, dict) or not 2 <= len(profiles) <= 6:
        fail("workflow needs two to six profiles")
    for name, config in profiles.items():
        if not PROFILE_RE.fullmatch(name) or not isinstance(config, dict) or set(config) != PROFILE_FIELDS:
            fail(f"invalid profile contract: {name}")
        if not isinstance(config["title"], str) or not config["title"].strip() or not MODEL_RE.fullmatch(config["model"]):
            fail(f"invalid title or model: {name}")
    if profiles["orchestrator"]["model"] != "claude-sonnet-5":
        fail("orchestrator must use Sonnet 5")
    if any(config["model"] != "claude-haiku-4-5-20251001"
           for name, config in profiles.items() if name != "orchestrator"):
        fail("specialists must use Haiku 4.5")
    return data


def safe_file(path, uid, required=True):
    if path.is_symlink(): fail(f"unsafe managed file: {path}")
    if not path.exists():
        if required: fail(f"required file missing: {path}")
        return
    info = path.lstat()
    if not path.is_file() or info.st_uid != uid or info.st_nlink != 1:
        fail(f"unsafe managed file: {path}")


def read_yaml(path):
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    if data is None: data = {}
    if not isinstance(data, dict): fail(f"YAML root must be a mapping: {path}")
    return data


def yaml_bytes(data):
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode()


def atomic_bytes(path, data, uid, gid):
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data); output.flush(); os.fsync(output.fileno())
    os.chown(temporary, uid, gid); os.chmod(temporary, 0o600)
    return temporary


def prepare(home, contract, uid):
    prepared = []
    for name, policy in contract["profiles"].items():
        root = home / "profiles" / name
        if not root.is_dir() or root.is_symlink() or root.stat().st_uid != uid:
            fail(f"profile missing or unsafe: {name}")
        config_path, meta_path = root / "config.yaml", root / "profile.yaml"
        safe_file(config_path, uid); safe_file(meta_path, uid, required=False)
        config, meta = read_yaml(config_path), read_yaml(meta_path)
        model = config.setdefault("model", {})
        agent = config.setdefault("agent", {})
        platforms = config.setdefault("platform_toolsets", {})
        if not all(isinstance(value, dict) for value in (model, agent, platforms)):
            fail(f"managed config sections must be mappings: {name}")
        if (root / "mcp.json").exists() or (root / "mcp.json").is_symlink():
            fail(f"workflow profile must not configure MCP: {name}")
        model.update(provider="anthropic", default=policy["model"])
        config["fallback_providers"] = []
        delegation = config.setdefault("delegation", {})
        if not isinstance(delegation, dict): fail(f"delegation config must be a mapping: {name}")
        delegation["fallback_providers"] = []
        agent["bot_mode_protocol"] = True
        platforms["api_server"] = ["no_mcp"]
        meta["display_name"] = policy["title"]
        ui_meta = meta.setdefault("ui_meta", {})
        if not isinstance(ui_meta, dict): fail(f"ui_meta must be a mapping: {name}")
        bots = ui_meta.setdefault("hermes-bots", {})
        if not isinstance(bots, dict): fail(f"hermes-bots metadata must be a mapping: {name}")
        bots["title"] = policy["title"]
        prepared.extend(((config_path, yaml_bytes(config)), (meta_path, yaml_bytes(meta))))
    return prepared


def backup_data(home, contract, prepared):
    return {"schema_version":1,"workflow":contract["workflow"],"files":{
        str(path.relative_to(home)): (base64.b64encode(path.read_bytes()).decode() if path.exists() else None)
        for path, _ in prepared}}


def restore_files(home, backup, uid, gid):
    if set(backup) != {"schema_version", "workflow", "files"} or backup["schema_version"] != 1 \
            or backup["workflow"] != "pr-risk-council" or not isinstance(backup["files"], dict):
        fail("invalid workflow backup")
    staged = []
    for relative, encoded in backup["files"].items():
        parts = Path(relative).parts
        if len(parts) != 3 or parts[0] != "profiles" or parts[1] in {"", ".", ".."} \
                or parts[2] not in {"config.yaml", "profile.yaml"}:
            fail("workflow backup path escaped profile root")
        path = home / relative
        safe_file(path, uid, required=False)
        if encoded is None:
            staged.append((path, None))
        elif isinstance(encoded, str):
            staged.append((path, atomic_bytes(path, base64.b64decode(encoded, validate=True), uid, gid)))
        else: fail("invalid workflow backup content")
    for path, temporary in staged:
        if temporary is None: path.unlink(missing_ok=True)
        else: os.replace(temporary, path)


def state_file(home):
    return home / "workflow-backups" / "pr-risk-council.state"


def write_state(home, value, uid, gid):
    path = state_file(home); temporary = atomic_bytes(path, (value + "\n").encode(), uid, gid)
    os.replace(temporary, path)


def configure(home, contract, uid, gid, apply=False, backup_path=None, replace_fn=os.replace):
    prepared = prepare(home, contract, uid)
    if not apply:
        return {"workflow":contract["workflow"],"profiles":list(contract["profiles"]),
                "model_ceiling":"claude-sonnet-5","applied":False}
    backup_path = backup_path or home / "workflow-backups" / "pr-risk-council.json"
    if backup_path.exists(): fail(f"workflow backup already exists; restore first: {backup_path}")
    if backup_path.parent.exists() and (backup_path.parent.is_symlink() or not backup_path.parent.is_dir()
                                        or backup_path.parent.stat().st_uid != uid):
        fail(f"unsafe workflow backup directory: {backup_path.parent}")
    backup_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(backup_path.parent, uid, gid); os.chmod(backup_path.parent, 0o700)
    backup = backup_data(home, contract, prepared)
    backup_tmp = atomic_bytes(backup_path, (json.dumps(backup, sort_keys=True) + "\n").encode(), uid, gid)
    os.replace(backup_tmp, backup_path); write_state(home, "applying", uid, gid)
    staged = []
    try:
        staged = [(path, atomic_bytes(path, data, uid, gid)) for path, data in prepared]
        for path, temporary in staged: replace_fn(temporary, path)
    except Exception:
        for _, temporary in staged:
            if temporary.exists(): temporary.unlink()
        restore_files(home, backup, uid, gid); backup_path.unlink(missing_ok=True); state_file(home).unlink(missing_ok=True)
        raise
    write_state(home, "applied", uid, gid)
    return {"workflow":contract["workflow"],"profiles":list(contract["profiles"]),
            "model_ceiling":"claude-sonnet-5","applied":True,"backup":str(backup_path)}


def restore(home, uid, gid, backup_path=None):
    backup_path = backup_path or home / "workflow-backups" / "pr-risk-council.json"
    safe_file(backup_path, uid)
    backup = json.loads(backup_path.read_text()); write_state(home, "restoring", uid, gid)
    restore_files(home, backup, uid, gid)
    backup_path.unlink(); state_file(home).unlink(missing_ok=True)
    return {"workflow":"pr-risk-council","restored":True}


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
        contract = load_contract(args.contract)
        user = pwd.getpwnam(args.service_user)
        if (args.apply or args.restore) and os.geteuid() != user.pw_uid:
            fail("apply/restore must run as the service user")
        result = (restore(args.hermes_home, user.pw_uid, user.pw_gid) if args.restore else
                  configure(args.hermes_home, contract, user.pw_uid, user.pw_gid, args.apply))
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as error:
        raise SystemExit(f"Hermes workflow configuration failed: {error}")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
