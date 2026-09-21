#!/usr/bin/env python3
"""Provision stable profile-scoped Hermes API keys and listener config.

Runs as root from hermes-native sync-support after profiles are installed. Keys are generated once,
kept outside CODE_ROOT/git in an owner-only JSON bundle, copied into each installed profile's private
.env, and never printed. Existing provider/profile env entries are preserved. Rotation is deliberately
not implemented here: approved DD requires pause+drain+preflight before auth generation changes.
"""
import argparse
import json
import os
import re
import secrets
import stat
import subprocess
from pathlib import Path

PROFILES = (
    "pr-review-v1", "pr-maintain-v1", "swe-implement-v1",
    "doc-write-v1", "memory-curate-v1", "pr-safety-v1",
)
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{40,}$")

def fail(message):
    raise SystemExit(f"Hermes API configuration: {message}")


def validate_private_parent(path: Path):
    info = path.parent.stat()
    allowed = {0, os.geteuid()}
    if os.environ.get("SUDO_UID", "").isdigit():
        allowed.add(int(os.environ["SUDO_UID"]))
    if info.st_uid not in allowed or stat.S_IMODE(info.st_mode) & 0o077:
        fail(f"key bundle parent must be owner-only: {path.parent}")


def load_or_create(path: Path):
    validate_private_parent(path)
    expected = {"schema_version", "auth_generation", "profiles"}
    if path.exists():
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            fail("existing key bundle must be regular mode 0600")
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            fail("existing key bundle is invalid JSON")
        if set(data) != expected or data["schema_version"] != 1 or data["auth_generation"] < 1:
            fail("existing key bundle shape changed")
        if set(data["profiles"]) != set(PROFILES):
            fail("existing key bundle profile set changed; rotate through approved procedure")
        keys = list(data["profiles"].values())
        if any(not isinstance(key, str) or not KEY_RE.fullmatch(key) for key in keys) or len(keys) != len(set(keys)):
            fail("existing key bundle contains weak or duplicate keys")
        return data
    data = {
        "schema_version": 1,
        "auth_generation": 1,
        "profiles": {name: secrets.token_urlsafe(32) for name in PROFILES},
    }
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(data, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
        output.flush(); os.fsync(output.fileno())
    os.replace(temporary, path)
    return data


def rewrite_env(path: Path, values: dict[str, str], uid: int, gid: int):
    lines = path.read_text().splitlines() if path.exists() else []
    managed = set(values)
    kept = [line for line in lines if not ("=" in line and line.split("=", 1)[0] in managed)]
    content = "\n".join([*kept, *(f"{key}={value}" for key, value in values.items())]) + "\n"
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(content); output.flush(); os.fsync(output.fileno())
    os.chown(temporary, uid, gid); os.chmod(temporary, 0o600); os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", required=True, type=Path)
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--launcher", required=True, type=Path)
    parser.add_argument("--keys-file", required=True, type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        fail("run as root")
    resolved_keys = args.keys_file.resolve()
    resolved_repo = args.repo_root.resolve()
    if resolved_keys == resolved_repo or resolved_repo in resolved_keys.parents \
            or any((parent / ".git").exists() for parent in (resolved_keys.parent, *resolved_keys.parents)):
        fail("key bundle must be outside the repository and Git parents")
    import pwd
    user = pwd.getpwnam(args.service_user)
    operator_uid = int(os.environ.get("SUDO_UID", "0"))
    operator = pwd.getpwuid(operator_uid)
    args.keys_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(args.keys_file.parent, operator.pw_uid, operator.pw_gid)
    os.chmod(args.keys_file.parent, 0o700)
    data = load_or_create(args.keys_file)
    os.chown(args.keys_file, operator.pw_uid, operator.pw_gid)
    os.chmod(args.keys_file, 0o600)
    root_env = args.hermes_home / ".env"
    listener_key = ""
    if root_env.exists():
        for line in root_env.read_text().splitlines():
            if line.startswith("API_SERVER_KEY="):
                listener_key = line.split("=", 1)[1].strip()
    if not KEY_RE.fullmatch(listener_key):
        listener_key = secrets.token_urlsafe(32)
    if listener_key in data["profiles"].values():
        fail("default listener key duplicates a profile key")
    rewrite_env(root_env, {
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": "127.0.0.1",
        "API_SERVER_PORT": "8642",
        "API_SERVER_KEY": listener_key,
        "GATEWAY_MULTIPLEX_PROFILES": "true",
    }, user.pw_uid, user.pw_gid)
    for profile in PROFILES:
        home = args.hermes_home / "profiles" / profile
        if not home.is_dir() or home.is_symlink():
            fail(f"installed profile missing: {profile}")
        rewrite_env(home / ".env", {"API_SERVER_KEY": data["profiles"][profile]}, user.pw_uid, user.pw_gid)
    command = ["sudo", "-u", args.service_user, "env", f"HOME={user.pw_dir}",
               f"HERMES_HOME={args.hermes_home}", str(args.launcher), "config", "set", "--force"]
    key, value = "gateway.api_server.max_concurrent_runs", "10"
    result = subprocess.run([*command, key, value], capture_output=True, text=True, timeout=30)
    if result.returncode:
        fail(f"could not set {key}: {result.stderr.strip() or result.stdout.strip()}")
    print(f"configured Hermes Runs API for {len(PROFILES)} profiles; auth_generation={data['auth_generation']}")


if __name__ == "__main__":
    main()
