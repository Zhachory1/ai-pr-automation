#!/usr/bin/env python3
"""Submit one actionable feedback snapshot to the Hermes maintenance profile."""
import json
import os
from pathlib import Path
import sys

from hermes_run_request import submit


def main():
    if len(sys.argv) != 5:
        raise SystemExit("usage: hermes-maintain-submit.py OWNER/REPO PR HEAD_SHA FEEDBACK_DIGEST")
    key = Path(os.environ.get("HERMES_MAINTAIN_KEY_FILE", "/run/secrets/hermes_maintain_key")).read_text().strip()
    if not key:
        raise ValueError("Hermes maintenance key is empty")
    result = submit("pr-maintain", sys.argv[1], int(sys.argv[2]), sys.argv[3], key,
                    feedback_digest=sys.argv[4],
                    base_url=os.environ.get("HERMES_API_BASE_URL", "http://host.docker.internal:8642"))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"hermes-maintain-submit: {error}") from None
