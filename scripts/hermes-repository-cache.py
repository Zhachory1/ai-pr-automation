#!/usr/bin/env python3
"""Enroll, synchronize, and inspect host-managed read-only repository mirrors."""
import argparse, fcntl, grp, json, os, pathlib, re, shutil, subprocess, tarfile, tempfile, time, urllib.parse

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


def configure_permissions(group):
    if not group: return
    if os.geteuid() != 0: fail("reader group requires root")
    try: gid = grp.getgrnam(group).gr_gid
    except KeyError as error: raise ValueError("reader group does not exist") from error
    os.setegid(gid); os.umask(0o027)


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


def inspect(root, repo, duration_ms=0, fetched_at=None):
    mirror, manifest, _ = paths(root, repo)
    if mirror.is_symlink() or not mirror.is_dir() or git(mirror, "rev-parse", "--is-bare-repository") != "true": fail("repository mirror is missing or invalid")
    remote = git(mirror, "remote", "get-url", "origin")
    branch_ref = git(mirror, "symbolic-ref", "HEAD")
    if not branch_ref.startswith("refs/heads/"): fail("mirror HEAD is not a branch")
    value = {"repository": repo, "mirror": str(mirror), "remote": remote, "default_branch": branch_ref.removeprefix("refs/heads/"), "head_sha": git(mirror, "rev-parse", "HEAD"), "fetched_at": int(time.time()) if fetched_at is None else fetched_at, "fetch_duration_ms": duration_ms, "size_bytes": disk_bytes(mirror)}
    atomic_json(manifest, value)
    return value


def snapshot_path(root, repo, sha):
    owner, name = repo.split("/")
    return root / owner / f"{name}.snapshots" / sha


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
        return inspect(root, repo, fetched_at=0 if args.seed else None)


def remote_default_ref(mirror):
    output = git(mirror, "ls-remote", "--symref", "origin", "HEAD")
    first = output.splitlines()[0] if output else ""
    match = re.fullmatch(r"ref: (refs/heads/[A-Za-z0-9._/-]+)\tHEAD", first)
    if not match: fail("remote default branch could not be resolved")
    return match.group(1)


def sync(args):
    root, repo = safe_root(args.root), identity(args.repository)
    mirror, manifest, lock = paths(root, repo)
    with locked(lock):
        if not manifest.is_file(): fail("repository is not enrolled")
        enrolled = json.loads(manifest.read_text())
        if git(mirror, "remote", "get-url", "origin") != enrolled.get("remote"): fail("mirror remote drift detected")
        started = time.monotonic()
        git(mirror, "remote", "update", "--prune")
        git(mirror, "symbolic-ref", "HEAD", remote_default_ref(mirror))
        return inspect(root, repo, round((time.monotonic() - started) * 1000))


def materialize(args):
    root, repo = safe_root(args.root), identity(args.repository)
    mirror, manifest, lock = paths(root, repo)
    with locked(lock):
        if not manifest.is_file(): fail("repository is not enrolled")
        value = json.loads(manifest.read_text())
        if not value.get("fetched_at"): fail("repository must be synchronized before materialization")
        sha = value["head_sha"]; destination = snapshot_path(root, repo, sha)
        if destination.is_symlink(): fail("repository snapshot must not be a symlink")
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = pathlib.Path(tempfile.mkdtemp(prefix=f".{sha}.", dir=destination.parent))
            env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}
            process = subprocess.Popen(["git", f"--git-dir={mirror}", "archive", "--format=tar", sha], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                try:
                    with tarfile.open(fileobj=process.stdout, mode="r|") as archive: archive.extractall(temporary, filter="data")
                except tarfile.TarError as error: raise ValueError("repository archive contains unsafe paths") from error
                if process.wait(timeout=1800): fail("git archive failed")
                os.chmod(temporary, 0o750)
                os.replace(temporary, destination)
            finally:
                if process.stdout: process.stdout.close()
                if process.poll() is None: process.kill(); process.wait()
                shutil.rmtree(temporary, ignore_errors=True)
        value["snapshot"] = str(destination); value["snapshot_sha"] = sha; value["snapshot_size_bytes"] = disk_bytes(destination)
        atomic_json(manifest, value)
        return value


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
    snapshot = pathlib.Path(value.get("snapshot", ""))
    value["snapshot_ready"] = bool(value.get("snapshot_sha") == value.get("head_sha") and snapshot.is_dir() and not snapshot.is_symlink())
    return value


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT); parser.add_argument("--reader-group")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("enroll"); add.add_argument("repository"); add.add_argument("--remote"); add.add_argument("--seed", type=pathlib.Path); add.set_defaults(handler=enroll)
    update = commands.add_parser("sync"); update.add_argument("repository"); update.set_defaults(handler=sync)
    snapshot = commands.add_parser("materialize"); snapshot.add_argument("repository"); snapshot.set_defaults(handler=materialize)
    show = commands.add_parser("status"); show.add_argument("repository"); show.add_argument("--max-age-seconds", type=int); show.set_defaults(handler=status)
    args = parser.parse_args()
    try:
        configure_permissions(args.reader_group)
        print(json.dumps(args.handler(args), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error: raise SystemExit(f"Hermes repository cache failed: {error}")


if __name__ == "__main__": main()
