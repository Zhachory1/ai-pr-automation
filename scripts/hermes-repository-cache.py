#!/usr/bin/env python3
"""Enroll, synchronize, and inspect host-managed read-only repository mirrors."""
import argparse, fcntl, json, os, pathlib, re, shutil, subprocess, tempfile, time, urllib.parse

DEFAULT_ROOT = pathlib.Path("/Users/Shared/ai-pr-automation-runtime/repositories")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def fail(message): raise ValueError(message)


def identity(value):
    if not REPO.fullmatch(value) or any(part in {".", ".."} for part in value.split("/")): fail("repository must be OWNER/REPO")
    return value


def validate_remote(remote, repo):
    parsed = urllib.parse.urlparse(remote)
    if parsed.username or parsed.password: fail("remote URL must not contain credentials")
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote)
    if match and f"{match.group(1)}/{match.group(2)}" != repo: fail("remote GitHub identity differs from repository")
    return remote


def paths(root, repo):
    owner, name = repo.split("/")
    base = root / owner / name
    return base.with_suffix(".git"), base.with_suffix(".json"), base.with_suffix(".lock")


def safe_root(root):
    root = root.expanduser().absolute()
    if root.is_symlink(): fail("cache root must not be a symlink")
    root = root.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def run(args, cwd=None):
    env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}
    completed = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=1800)
    if completed.returncode: fail(f"git command failed: {args[1] if len(args) > 1 else 'git'}")
    return completed.stdout.strip()


def git(mirror, *args): return run(["git", f"--git-dir={mirror}", *args])


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as target:
            json.dump(value, target, sort_keys=True, separators=(",", ":")); target.write("\n")
            target.flush(); os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def disk_bytes(path):
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def inspect(root, repo, duration_ms=0):
    mirror, manifest, _ = paths(root, repo)
    if mirror.is_symlink() or not mirror.is_dir() or git(mirror, "rev-parse", "--is-bare-repository") != "true": fail("repository mirror is missing or invalid")
    remote = git(mirror, "remote", "get-url", "origin")
    branch_ref = git(mirror, "symbolic-ref", "HEAD")
    if not branch_ref.startswith("refs/heads/"): fail("mirror HEAD is not a branch")
    value = {"repository": repo, "mirror": str(mirror), "remote": remote, "default_branch": branch_ref.removeprefix("refs/heads/"), "head_sha": git(mirror, "rev-parse", "HEAD"), "fetched_at": int(time.time()), "fetch_duration_ms": duration_ms, "size_bytes": disk_bytes(mirror)}
    atomic_json(manifest, value)
    return value


def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def enroll(args):
    root, repo = safe_root(args.root), identity(args.repository)
    mirror, _, lock = paths(root, repo)
    remote = validate_remote(args.remote or f"https://github.com/{repo}.git", repo)
    with locked(lock):
        if mirror.exists():
            if mirror.is_symlink() or git(mirror, "remote", "get-url", "origin") != remote: fail("enrolled mirror remote differs from requested remote")
            _, manifest, _ = paths(root, repo)
            return json.loads(manifest.read_text()) if manifest.is_file() else inspect(root, repo)
        mirror.parent.mkdir(parents=True, exist_ok=True)
        temporary = pathlib.Path(tempfile.mkdtemp(prefix=f".{mirror.name}.", dir=mirror.parent))
        staged = temporary / mirror.name
        try:
            source = str(args.seed.expanduser().resolve()) if args.seed else remote
            run(["git", "clone", "--mirror", "--no-local", source, str(staged)])
            run(["git", f"--git-dir={staged}", "remote", "set-url", "origin", remote])
            os.replace(staged, mirror)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        return inspect(root, repo)


def sync(args):
    root, repo = safe_root(args.root), identity(args.repository)
    mirror, manifest, lock = paths(root, repo)
    with locked(lock):
        if not manifest.is_file(): fail("repository is not enrolled")
        enrolled = json.loads(manifest.read_text())
        if git(mirror, "remote", "get-url", "origin") != enrolled.get("remote"): fail("mirror remote drift detected")
        started = time.monotonic()
        git(mirror, "remote", "update", "--prune")
        return inspect(root, repo, round((time.monotonic() - started) * 1000))


def status(args):
    root, repo = safe_root(args.root), identity(args.repository)
    _, manifest, _ = paths(root, repo)
    if not manifest.is_file(): fail("repository is not enrolled")
    value = json.loads(manifest.read_text())
    mirror, _, _ = paths(root, repo)
    if mirror.is_symlink() or git(mirror, "rev-parse", "--is-bare-repository") != "true": fail("repository mirror is missing or invalid")
    if git(mirror, "remote", "get-url", "origin") != value.get("remote"): fail("mirror remote drift detected")
    if args.max_age_seconds is not None and args.max_age_seconds < 0: fail("max age must be non-negative")
    value["age_seconds"] = max(0, int(time.time()) - int(value["fetched_at"]))
    value["stale"] = bool(args.max_age_seconds is not None and value["age_seconds"] > args.max_age_seconds)
    return value


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("enroll"); add.add_argument("repository"); add.add_argument("--remote"); add.add_argument("--seed", type=pathlib.Path); add.set_defaults(handler=enroll)
    update = commands.add_parser("sync"); update.add_argument("repository"); update.set_defaults(handler=sync)
    show = commands.add_parser("status"); show.add_argument("repository"); show.add_argument("--max-age-seconds", type=int); show.set_defaults(handler=status)
    args = parser.parse_args()
    try: print(json.dumps(args.handler(args), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error: raise SystemExit(f"Hermes repository cache failed: {error}")


if __name__ == "__main__": main()
