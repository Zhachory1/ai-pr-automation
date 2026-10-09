"""Content-free, one-attempt-per-day inbox manifest; no live Gmail adapter."""

from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
import fcntl
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import subprocess

from hermes_inbox_reader import InboxOverflow, previous_day, scan


MAX_MESSAGES = 250


def _private_parent(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if path.parent.is_symlink() or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ValueError("inbox state directory must be owner-only")


@contextmanager
def lock_manifest(path: Path):
    lock = Path(f"{path}.lock")
    _private_parent(lock)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("inbox lock must be owner-only")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def kanban_notice(hermes_bin: str, day: str, reason: str) -> None:
    title = f"Inbox needs attention: {day} ({reason})"
    body = f"Inbox run {day} stopped: {reason}. No automatic backfill; inspect the local manifest before replay."
    command = [hermes_bin, "kanban", "--board", "inbox-replies", "create", title,
               "--body", body, "--initial-status", "blocked", "--created-by", "inbox-controller",
               "--idempotency-key", f"inbox-alert-{day}-{reason}"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError("Kanban inbox notice failed")


def open_manifest(path: Path) -> sqlite3.Connection:
    path = Path(path)
    _private_parent(path)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = None
    if fd is not None:
        os.close(fd)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("inbox manifest must be owner-only")
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("""CREATE TABLE IF NOT EXISTS days (
        day TEXT PRIMARY KEY, status TEXT NOT NULL, started_at INTEGER, lease TEXT,
        count INTEGER, reason TEXT, notified INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute("CREATE TABLE IF NOT EXISTS account_binding (id INTEGER PRIMARY KEY CHECK(id=1), account TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS seen (message_id TEXT PRIMARY KEY, outcome TEXT NOT NULL)")
    return conn


def _notify(conn, notice, through: str) -> bool:
    failed = False
    for row in conn.execute(
        "SELECT day, reason FROM days WHERE status = 'failed' AND notified = 0 AND day <= ? ORDER BY day",
        (through,),
    ):
        try:
            notice(row["day"], row["reason"])
        except Exception:
            failed = True
            continue
        with conn:
            conn.execute("UPDATE days SET notified = 1 WHERE day = ? AND status = 'failed'", (row["day"],))
    return failed


def run(path: Path, gmail, now: datetime, account: str, since: date, notice, admit) -> int:
    """Scan yesterday once; report older missing dates but never backfill them automatically."""
    with lock_manifest(path) as acquired:
        if not acquired:
            return 0
        return _run_locked(path, gmail, now, account, since, notice, admit)


def replay_failed(path: Path, gmail, now: datetime, account: str, day: date, notice, admit) -> int:
    """Explicit same-day replay only; never called by the daily cron."""
    if day > previous_day(now)[0]:
        raise ValueError("replay date must precede today in New York")
    with lock_manifest(path) as acquired:
        if not acquired:
            raise RuntimeError("inbox run already active")
        return _run_locked(path, gmail, now, account, day, notice, admit, replay_day=day)


def _run_locked(path: Path, gmail, now: datetime, account: str, since: date, notice, admit,
                replay_day: date | None = None) -> int:
    day = replay_day or previous_day(now)[0]
    if replay_day is None and since > day:
        raise ValueError("launch day is after yesterday")
    key, lease = day.isoformat(), secrets.token_hex(16)
    with closing(open_manifest(path)) as conn:
        with conn:
            conn.execute("INSERT OR IGNORE INTO account_binding(id,account) VALUES (1,?)", (account.casefold(),))
            bound = conn.execute("SELECT account FROM account_binding WHERE id=1").fetchone()[0]
            if bound != account.casefold():
                raise ValueError("inbox manifest account mismatch")
            if replay_day is None:
                missing = since
                while missing < day:
                    conn.execute(
                        "INSERT OR IGNORE INTO days(day,status,reason) VALUES (?,'failed','missed-run')",
                        (missing.isoformat(),),
                    )
                    conn.execute(
                        "UPDATE days SET status = 'failed', reason = 'abandoned' "
                        "WHERE day = ? AND status = 'started'",
                        (missing.isoformat(),),
                    )
                    missing += timedelta(days=1)
                conn.execute(
                    "UPDATE days SET status = 'failed', reason = 'abandoned' "
                    "WHERE day = ? AND status = 'started'",
                    (key,),
                )
            elif not conn.execute(
                "SELECT 1 FROM days WHERE day = ? AND status = 'failed'", (key,)
            ).fetchone():
                raise ValueError("only a failed inbox day may be manually replayed")
        notice_failed = _notify(conn, notice, key)
        with conn:
            if replay_day is None:
                created = conn.execute(
                    "INSERT OR IGNORE INTO days(day,status,started_at,lease) VALUES (?,'started',?,?)",
                    (key, int(now.timestamp()), lease),
                ).rowcount
            else:
                created = conn.execute(
                    "UPDATE days SET status = 'started', reason = NULL, count = NULL, notified = 0, "
                    "started_at = ?, lease = ? WHERE day = ? AND status = 'failed'",
                    (int(now.timestamp()), lease, key),
                ).rowcount
        if not created:
            if notice_failed:
                raise RuntimeError("inbox notice unavailable")
            return 0
        phase = "reader-error"
        try:
            messages = scan(gmail, now, account, limit=MAX_MESSAGES, day=day)
            phase = "admission-error"
            processed = 0
            for message in messages:
                message_id = message["id"]
                if conn.execute("SELECT 1 FROM seen WHERE message_id = ?", (message_id,)).fetchone():
                    continue
                dedupe = hashlib.sha256(f"{account.casefold()}\0{message_id}".encode()).hexdigest()
                outcome = admit(key, message, f"inbox-message-{dedupe}")
                if outcome not in ("admitted", "skipped"):
                    raise ValueError("admission must return admitted or skipped")
                with conn:
                    conn.execute("INSERT INTO seen(message_id,outcome) VALUES (?,?)", (message_id, outcome))
                processed += 1
        except Exception as error:
            reason = "overflow" if isinstance(error, InboxOverflow) else phase
            with conn:
                conn.execute(
                    "UPDATE days SET status = 'failed', reason = ? WHERE day = ? AND status = 'started' AND lease = ?",
                    (reason, key, lease),
                )
            _notify(conn, notice, key)
            raise
        with conn:
            updated = conn.execute(
                "UPDATE days SET status = 'complete', count = ? WHERE day = ? AND status = 'started' AND lease = ?",
                (len(messages), key, lease),
            ).rowcount
        if updated != 1:
            raise RuntimeError("inbox day lease lost during scan")
        if notice_failed:
            raise RuntimeError("inbox notice unavailable")
        return processed
