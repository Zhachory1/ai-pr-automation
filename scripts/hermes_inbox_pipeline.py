"""Compose the narrow inbox reader, Luna classifier, and Kanban admission."""

from datetime import date, datetime
from pathlib import Path
import time

import hermes_inbox_admit as intake
import hermes_inbox_daily as daily
import hermes_inbox_luna as luna


def run_once(manifest: Path, gmail, hermes_bin: str, account: str, since: date, now: datetime,
             *, workdir: Path | None = None, deadline: float | None = None) -> int:
    """Run only when the local board, profiles, and operator alert path are ready."""
    workdir = workdir or Path(manifest).parent

    def admit(day, message, key):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("daily inbox work budget exhausted")
        return intake.admit(hermes_bin, account,
                            lambda candidate: luna.classify(hermes_bin, workdir, day, candidate),
                            day, message, key)

    return daily.run(manifest, gmail, now, account, since,
                     lambda day, reason: daily.kanban_notice(hermes_bin, day, reason), admit)
