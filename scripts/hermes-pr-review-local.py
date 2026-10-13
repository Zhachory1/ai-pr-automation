#!/usr/bin/env python3
"""Find an existing checkout and pin a PR head without cloning or switching branches."""
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


def resolve(repository, head_sha, *, root=None, fetch=True):
    if not isinstance(repository, str) or not REPO.fullmatch(repository) or any(
            part in (".", "..") for part in repository.split("/")) or not SHA.fullmatch(head_sha):
        raise ValueError("invalid local review repository or commit")
    root = Path.home() / "code" if root is None else Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("local checkout root is unavailable")
    root = root.resolve(strict=True)
    owner, name = repository.split("/")
    candidates = [root / name, root / owner / name]
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_LFS_SKIP_SMUDGE="1")

    def git(path, *args, timeout=10):
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                              env=env, timeout=timeout, check=False)

    for candidate in dict.fromkeys(candidates):
        if candidate.is_symlink() or not (candidate / ".git").exists():
            continue
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(root):
            continue
        remote = git(path, "remote", "get-url", "origin")
        if remote.returncode:
            continue
        url = remote.stdout.decode("utf-8", "replace").strip()
        if url.startswith("git@github.com:"):
            found = url.split(":", 1)[1]
        else:
            parsed = urlsplit(url)
            if parsed.scheme not in ("https", "ssh") or parsed.hostname != "github.com" \
                    or parsed.password or parsed.scheme == "ssh" and parsed.username != "git":
                continue
            found = parsed.path.lstrip("/")
        if found.removesuffix(".git").lower() != repository.lower():
            continue
        if git(path, "rev-parse", "--show-toplevel").stdout.decode().strip() != str(path):
            continue
        commit = f"{head_sha}^{{commit}}"
        if git(path, "cat-file", "-e", commit).returncode:
            if not fetch or git(path, "fetch", "--no-tags", "--no-write-fetch-head", "origin", head_sha,
                                timeout=120).returncode:
                raise ValueError("review head is not available in local checkout")
            if git(path, "cat-file", "-e", commit).returncode:
                raise ValueError("review head was not fetched")
        return path
    raise ValueError("matching local checkout not found; no clone was created")
