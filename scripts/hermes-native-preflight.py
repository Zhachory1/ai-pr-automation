#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path


def fail(message):
    raise SystemExit(f"Hermes native preflight: {message}")


def regular(path):
    try:
        info = path.lstat()
    except OSError:
        fail(f"missing {path.name}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"{path.name} must be a regular non-symlink file")
    return path


def digest(path):
    return hashlib.sha256(regular(path).read_bytes()).hexdigest()


def profile_digest(root):
    value = hashlib.sha256()
    for name in (".no-bundled-skills", "SOUL.md", "config.yaml", "distribution.yaml"):
        path = regular(root / name)
        value.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    return value.hexdigest()


def run(*command, env=None):
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        fail(f"command failed: {Path(command[0]).name}")
    if result.returncode:
        fail(f"command failed: {Path(command[0]).name}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--profile-source", type=Path, required=True)
    args = parser.parse_args()

    contract = {}
    for line in regular(args.contract).read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            contract[key] = value
    manifest = json.loads(regular(args.manifest).read_text())
    expected = {"version", "commit", "installer_sha256", "launcher_sha256", "profile_digest"}
    if set(manifest) != expected:
        fail("install manifest shape changed")
    if (manifest["version"] != contract["HERMES_NATIVE_VERSION"]
            or manifest["commit"] != contract["HERMES_NATIVE_COMMIT"]
            or manifest["installer_sha256"] != contract["HERMES_INSTALLER_SHA256"]):
        fail("install manifest does not match contract")

    install = args.install_dir.resolve()
    home = args.hermes_home.resolve()
    launcher = regular(home.parent / ".local/bin/hermes")
    git = ("git", "-c", f"safe.directory={install}", "-C", str(install))
    if run(*git, "rev-parse", "HEAD").strip() != manifest["commit"]:
        fail("installed checkout commit changed")
    if run(*git, "status", "--porcelain", "--untracked-files=no").strip():
        fail("installed checkout has tracked changes")
    if digest(launcher) != manifest["launcher_sha256"]:
        fail("Hermes launcher digest changed")
    if profile_digest(args.profile_source.resolve()) != manifest["profile_digest"]:
        fail("smoke profile source changed")
    installed_profile = home / "profiles/smoke-v1"
    if profile_digest(installed_profile) != manifest["profile_digest"]:
        fail("installed smoke profile changed")

    environment = os.environ | {"HOME": str(home.parent), "HERMES_HOME": str(home)}
    version = run(str(launcher), "--version", env=environment)
    if contract["HERMES_NATIVE_VERSION"] not in version:
        fail("Hermes version output does not match contract")
    run(str(launcher), "-p", "smoke-v1", "profile", "show", "smoke-v1", env=environment)
    print(json.dumps({"status": "ready", "version": manifest["version"], "commit": manifest["commit"],
                      "profile": "smoke-v1"}, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
