#!/usr/bin/env python3
"""Read-only installed-byte and launch configuration preflight for safety bridge."""
import argparse
import hashlib
import json
import os
import plistlib
import pwd
import re
import stat
import subprocess
import tempfile
from pathlib import Path

DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def fail(message):
    raise SystemExit(f"Hermes Kanban safety bridge preflight: {message}")


def checked(path, uid, mode, directory=False, gid=None):
    try: info = path.lstat()
    except OSError: fail(f"missing {path}")
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if stat.S_ISLNK(info.st_mode) or not expected or info.st_uid != uid \
            or (gid is not None and info.st_gid != gid) or stat.S_IMODE(info.st_mode) != mode \
            or (not directory and info.st_nlink != 1):
        fail(f"unsafe ownership or mode: {path}")
    return path


def service_can_read(path, user):
    groups = set(os.getgrouplist(user.pw_name, user.pw_gid))
    def allowed(info, owner, group, other):
        bits = stat.S_IMODE(info.st_mode)
        return bool(bits & (owner if info.st_uid == user.pw_uid else group if info.st_gid in groups else other))
    try:
        readable = allowed(path.stat(), stat.S_IRUSR, stat.S_IRGRP, stat.S_IROTH)
        traversable = all(allowed(parent.stat(), stat.S_IXUSR, stat.S_IXGRP, stat.S_IXOTH)
                          for parent in path.parents)
    except OSError:
        return False
    return readable and traversable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--reconcile", type=Path, required=True)
    parser.add_argument("--risk-council", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--runtime-contract", type=Path, required=True)
    parser.add_argument("--self-path", type=Path, required=True)
    parser.add_argument("--plist", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--workflow-root", type=Path, required=True)
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--policy-path", type=Path, required=True)
    parser.add_argument("--policy-version", required=True)
    parser.add_argument("--policy-digest", required=True)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host-header", default="hermes-council.localhost:8766")
    parser.add_argument("--root-uid", type=int, default=0)
    parser.add_argument("--staff-gid", type=int, default=20)
    parser.add_argument("--installed-source", action="append", default=[])
    args = parser.parse_args()

    try: service = pwd.getpwnam(args.service_user)
    except KeyError: fail("service user missing")
    root_uid, service_uid = args.root_uid, service.pw_uid
    for path, mode in ((args.bridge,0o555),(args.reconcile,0o555),(args.risk_council,0o555),
                       (args.self_path,0o555),(args.contract,0o444),(args.runtime_contract,0o444)):
        checked(path, root_uid, mode)
    checked(args.plist, root_uid, 0o644)
    checked(args.key_file.parent, root_uid, 0o750, True, args.staff_gid)
    checked(args.key_file, service_uid, 0o600)
    if not service_can_read(args.key_file, service): fail("service user cannot traverse/read bridge key")
    checked(args.state_root, service_uid, 0o700, True)
    checked(args.state_root / "workflows", service_uid, 0o700, True)
    checked(args.workflow_root, service_uid, 0o700, True)
    checked(args.snapshot_root, root_uid, 0o750, True, args.staff_gid)
    checked(args.policy_path, root_uid, 0o444)
    if not DIGEST_RE.fullmatch(args.policy_digest) \
            or hashlib.sha256(args.policy_path.read_bytes()).hexdigest() != args.policy_digest:
        fail("policy digest mismatch")
    for pair in args.installed_source:
        try: installed, source = map(Path, pair.split("=", 1))
        except ValueError: fail("invalid installed-source pair")
        if installed.read_bytes() != source.read_bytes(): fail(f"installed bytes differ: {installed.name}")

    try: key = json.loads(args.key_file.read_text())
    except (OSError, json.JSONDecodeError): fail("invalid key JSON")
    if set(key) != {"schema_version","auth_generation","key"} or key["schema_version"] != 1 \
            or type(key["auth_generation"]) is not int or key["auth_generation"] < 1 \
            or not isinstance(key["key"], str) or not DIGEST_RE.fullmatch(key["key"]):
        fail("invalid key schema")
    try: contract = json.loads(args.contract.read_text())
    except (OSError, json.JSONDecodeError): fail("invalid workflow contract")
    if contract.get("schema_version") != 2 or contract.get("workflow") != "pr-risk-council" \
            or contract.get("max_active_workflows") != 1 or contract.get("deadline_seconds") != 900:
        fail("workflow contract mismatch")

    with args.plist.open("rb") as source: plist = plistlib.load(source)
    expected_program = [str(args.install_dir / "venv/bin/python"), str(args.bridge)]
    if plist.get("UserName") != args.service_user or plist.get("ProgramArguments") != expected_program \
            or plist.get("RunAtLoad") is not False:
        fail("launchd identity or activation mismatch")
    env = plist.get("EnvironmentVariables", {})
    expected_env = {
        "HOME":str(args.hermes_home.parent),"HERMES_HOME":str(args.hermes_home),
        "HERMES_INSTALL_DIR":str(args.install_dir),"HERMES_KANBAN_BRIDGE_BIND":"127.0.0.1",
        "HERMES_KANBAN_BUSY_TIMEOUT_MS":"120000","HERMES_KANBAN_BRIDGE_PORT":str(args.port),"HERMES_KANBAN_BRIDGE_HOST_HEADER":args.host_header,
        "HERMES_KANBAN_BRIDGE_STATE_ROOT":str(args.state_root),"HERMES_KANBAN_BRIDGE_KEY_FILE":str(args.key_file),
        "HERMES_KANBAN_BRIDGE_CONTRACT":str(args.contract),"HERMES_KANBAN_RISK_COUNCIL":str(args.risk_council),
        "HERMES_NATIVE_CONTRACT":str(args.runtime_contract),"PR_SAFETY_WORKFLOW_ROOT":str(args.workflow_root),
        "PR_SAFETY_SNAPSHOT_ROOT":str(args.snapshot_root),"PR_SAFETY_POLICY_PATH":str(args.policy_path),
        "PR_SAFETY_POLICY_VERSION":args.policy_version,"PR_SAFETY_POLICY_DIGEST":args.policy_digest,
    }
    if env != expected_env: fail("launchd bridge configuration mismatch")
    if plist.get("KeepAlive") != {"SuccessfulExit":False}: fail("launchd keepalive mismatch")

    probe = r'''import json,sqlite3,sys
from pathlib import Path
from hermes_cli import kanban_db_connect as kbc
connection=kbc.connect(Path(sys.argv[1]))
try:
 print(json.dumps({"journal_mode":connection.execute("PRAGMA journal_mode").fetchone()[0],
                   "busy_timeout":connection.execute("PRAGMA busy_timeout").fetchone()[0],
                   "sqlite_version":sqlite3.sqlite_version},sort_keys=True,separators=(",",":")))
finally: connection.close()
'''
    environment = {"PATH":os.environ.get("PATH", ""),"PYTHONPATH":str(args.install_dir),
                   "PYTHONUTF8":"1","PYTHONDONTWRITEBYTECODE":"1","HERMES_HOME":str(args.hermes_home),
                   "HERMES_KANBAN_BUSY_TIMEOUT_MS":"120000"}
    try:
        with tempfile.TemporaryDirectory(prefix="hermes-kanban-bridge-preflight-") as temporary:
            completed = subprocess.run([args.install_dir / "venv/bin/python", "-B", "-c", probe,
                                        str(Path(temporary) / "kanban.db")], cwd=args.install_dir,
                                       env=environment, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired): fail("pinned SQLite runtime probe failed")
    if completed.returncode:
        fail(f"pinned SQLite runtime probe failed: {completed.stderr.strip()[:300]}")
    try: sqlite = json.loads(completed.stdout)
    except json.JSONDecodeError: fail("pinned SQLite runtime probe returned invalid evidence")
    if sqlite.get("journal_mode") != "delete" or sqlite.get("busy_timeout") != 120000:
        fail("pinned SQLite runtime settings changed")
    print(json.dumps({"status":"ready","auth_generation":key["auth_generation"],"port":args.port,
                      "sqlite":sqlite},sort_keys=True,separators=(",", ":")))


if __name__ == "__main__": main()
