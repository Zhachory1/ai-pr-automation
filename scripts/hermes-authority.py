#!/usr/bin/env python3
"""Read the operator's repo-authority allowlist.

Authority is scope-of-attention, not security: the hermes-agent account, repo-scoped deploy key,
read-only API token, and GitHub branch protection are the real boundary (see
docs/hermes/DD-authority-and-memory.md). This file just lists the repositories the operator has
granted the fleet permission to spend effort on. Producers consult it before enqueuing repo-scoped
work; the queue itself no longer authorizes.

YAML shape (no digests, no freshness, no proof):

    repos:
      - Zhachory1/ai-pr-automation
    # local roles (doc-write, memory-curate) are not repo-scoped and need no grant.

Default path: $HERMES_AUTHORITY_FILE or /Users/Shared/zhach-ai-pr-automation/authority.yaml
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT = os.environ.get("HERMES_AUTHORITY_FILE", "/Users/Shared/zhach-ai-pr-automation/authority.yaml")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def load(path):
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"authority file not found: {path}")
    # Minimal YAML: a `repos:` block of `- owner/repo` lines. Avoids a yaml dependency; rejects
    # anything that is not the expected shape rather than silently accepting junk.
    repos, in_repos = [], False
    for raw in p.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if line == "repos:":
            in_repos = True
            continue
        if in_repos and re.match(r"^\s*-\s+", line):
            repo = line.split("-", 1)[1].strip().strip('"').strip("'")
            if not REPO_RE.match(repo):
                raise SystemExit(f"invalid repo in authority file: {repo!r}")
            repos.append(repo)
        elif not line.startswith(" "):
            in_repos = (line == "repos:")
    return repos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=DEFAULT)
    parser.add_argument("--check", metavar="OWNER/REPO",
                        help="exit 0 if the repo is granted, 3 if not")
    args = parser.parse_args()
    repos = load(args.file)
    if args.check:
        if args.check in repos:
            print(json.dumps({"repo": args.check, "granted": True}))
            return
        print(json.dumps({"repo": args.check, "granted": False}), file=sys.stderr)
        raise SystemExit(3)
    print(json.dumps({"repos": repos}, separators=(",", ":")))


if __name__ == "__main__":
    main()
