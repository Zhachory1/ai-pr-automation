#!/usr/bin/env python3
"""Submit one discovered review head using the shared Hermes Runs API client."""
import json
import os
from pathlib import Path
import sys

from hermes_run_request import submit


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: hermes-review-submit.py OWNER/REPO PR HEAD_SHA")
    key = Path(os.environ.get("HERMES_REVIEW_KEY_FILE", "/run/secrets/hermes_review_key")).read_text().strip()
    if not key:
        raise ValueError("Hermes review key is empty")
    result = submit("pr-review", sys.argv[1], int(sys.argv[2]), sys.argv[3], key,
                    base_url=os.environ.get("HERMES_API_BASE_URL", "http://host.docker.internal:8642"))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"hermes-review-submit: {error}") from None
