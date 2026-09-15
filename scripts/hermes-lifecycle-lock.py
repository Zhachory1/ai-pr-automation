#!/usr/bin/env python3
import fcntl
import os
import signal
import subprocess
import sys
from pathlib import Path

if len(sys.argv) < 2:
    raise SystemExit("usage: hermes-lifecycle-lock.py <command> [args...]")

path = Path(os.environ.get("HERMES_OAUTH_LOCK_FILE", "/tmp/ai-pr-automation-hermes-oauth.lock"))
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("w") as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Hermes lifecycle operation already running")
    os.set_inheritable(lock.fileno(), True)
    child = subprocess.Popen(
        sys.argv[1:], env=os.environ | {"HERMES_OAUTH_LOCK_HELD": "1"},
        pass_fds=(lock.fileno(),), start_new_session=True,
    )

    def forward(number, _frame):
        if child.poll() is None:
            os.killpg(child.pid, number)

    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGTERM, forward)
    raise SystemExit(child.wait())
