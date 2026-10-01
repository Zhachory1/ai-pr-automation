#!/usr/bin/env python3
"""Create or verify one isolated SWE implementation clone from a pinned local mirror."""
import argparse, hashlib, json, os, pathlib, re, shutil, stat, subprocess, tempfile

REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
OPERATION = re.compile(r"^swe-implement-[0-9a-f]{64}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
BRANCH = re.compile(r"^hermes/[0-9a-f]{12}-[a-z0-9][a-z0-9-]{0,60}$")


def fail(message): raise ValueError(message)


def run(args, cwd=None):
    completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=1800, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"})
    if completed.returncode: fail(f"git command failed: {args[1] if len(args) > 1 else 'git'}")
    return completed.stdout.strip()


def safe_root(path):
    path = path.expanduser().absolute()
    if path.is_symlink(): fail("work root must not be a symlink")
    path = path.resolve(strict=False); path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700: fail("unsafe work root")
    return path


def validate(args):
    if not REPO.fullmatch(args.repository) or any(part in {".", ".."} for part in args.repository.split("/")): fail("repository must be OWNER/REPO")
    if not OPERATION.fullmatch(args.operation): fail("invalid operation")
    if not SHA.fullmatch(args.base_sha): fail("invalid base SHA")
    if not BRANCH.fullmatch(args.branch): fail("invalid branch")
    if args.branch.split("/", 1)[1][:12] != args.operation.removeprefix("swe-implement-")[:12]: fail("branch differs from operation")
    expected = f"https://github.com/{args.repository}.git"
    if args.remote != expected: fail("remote differs from enrolled repository")
    mirror = args.mirror.expanduser().resolve()
    if mirror.is_symlink() or not mirror.is_dir() or run(["git", f"--git-dir={mirror}", "rev-parse", "--is-bare-repository"]) != "true": fail("invalid mirror")
    if run(["git", f"--git-dir={mirror}", "rev-parse", args.pin_ref]) != args.base_sha: fail("mirror pin differs from base SHA")
    return mirror


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as target:
            os.fchmod(target.fileno(), 0o600); json.dump(value, target, sort_keys=True, separators=(",", ":")); target.write("\n"); target.flush(); os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def expected(args, root, mirror):
    operation_root = root / args.operation
    return {"operation": args.operation, "repository": args.repository, "base_sha": args.base_sha, "branch": args.branch, "remote": args.remote, "mirror": str(mirror), "pin_ref": args.pin_ref, "operation_root": str(operation_root), "worktree": str(operation_root / "repo")}


def verify_workspace(value):
    worktree = pathlib.Path(value["worktree"]); operation_root = pathlib.Path(value["operation_root"])
    if operation_root.is_symlink() or worktree.is_symlink() or not worktree.is_dir(): fail("workspace missing or unsafe")
    if operation_root.stat().st_uid != os.geteuid() or stat.S_IMODE(operation_root.stat().st_mode) != 0o700: fail("unsafe operation root")
    if run(["git", "status", "--porcelain"], worktree): fail("workspace is dirty")
    if run(["git", "rev-parse", "HEAD"], worktree) != value["base_sha"]: fail("workspace head drift")
    if run(["git", "branch", "--show-current"], worktree) != value["branch"]: fail("workspace branch drift")
    if run(["git", "config", "--get", "remote.origin.url"], worktree) != value["remote"]: fail("workspace remote drift")
    alternates = worktree / ".git/objects/info/alternates"
    if not alternates.is_file() or pathlib.Path(alternates.read_text().strip()).resolve() != pathlib.Path(value["mirror"]).resolve() / "objects": fail("workspace object source drift")
    return value


def prepare(args):
    root = safe_root(args.work_root); mirror = validate(args); value = expected(args, root, mirror)
    operation_root = pathlib.Path(value["operation_root"]); manifest = operation_root / "workspace.json"
    if operation_root.exists():
        if not manifest.is_file() or json.loads(manifest.read_text()) != value: fail("workspace identity collision")
        return verify_workspace(value)
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=f".{args.operation}.", dir=root)); staged = temporary / "repo"
    try:
        run(["git", "clone", "--shared", "--no-checkout", str(mirror), str(staged)])
        run(["git", "remote", "set-url", "origin", args.remote], staged)
        run(["git", "checkout", "-b", args.branch, args.base_sha], staged)
        os.chmod(temporary, 0o700)
        atomic_json(temporary / "workspace.json", value)
        os.replace(temporary, operation_root)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return verify_workspace(value)


def status(args):
    root = safe_root(args.work_root); mirror = validate(args); value = expected(args, root, mirror); manifest = pathlib.Path(value["operation_root"]) / "workspace.json"
    if not manifest.is_file() or json.loads(manifest.read_text()) != value: fail("workspace identity missing")
    return verify_workspace(value)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=("prepare", "status")); parser.add_argument("repository"); parser.add_argument("operation"); parser.add_argument("base_sha"); parser.add_argument("branch")
    parser.add_argument("--mirror", type=pathlib.Path, required=True); parser.add_argument("--pin-ref", required=True); parser.add_argument("--remote", required=True); parser.add_argument("--work-root", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try: print(json.dumps((prepare if args.command == "prepare" else status)(args), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error: raise SystemExit(f"Hermes SWE workspace failed: {error}")


if __name__ == "__main__": main()
