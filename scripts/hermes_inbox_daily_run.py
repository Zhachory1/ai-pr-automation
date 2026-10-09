"""Daily prior-day inbox intake, Sol staging, and Gmail Drafts creation; no send path."""

from contextlib import closing
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
import time

import yaml

import hermes_inbox_controller as controller
import hermes_inbox_daily as daily
import hermes_inbox_gmail as gmail
import hermes_inbox_gmail_drafts as draft_api
import hermes_inbox_gmail_draft_publish as publisher
import hermes_inbox_pipeline as pipeline
import hermes_inbox_reader as reader


class RunnerBusy(RuntimeError):
    pass


MAX_RUN_SECONDS = 6 * 60 * 60
SECOND_ACCOUNT = "zhackymoto@gmail.com"


def run_cards(board, ledger, clients, hermes_bin, workdir, day, *, deadline=None):
    rows = board.execute(
        "SELECT id,body,assignee FROM tasks WHERE status='blocked' AND created_by='inbox-intake' "
        "ORDER BY created_at,id"
    ).fetchall()
    def due(row):
        value = json.loads(row["body"])
        if not isinstance(value, dict):
            raise ValueError("inbox card body is not an object")
        source_day = value.get("source_day")
        if not (isinstance(source_day, str) and len(source_day) == 10 and source_day <= day.isoformat()
                and date.fromisoformat(source_day).isoformat() == source_day):
            return None
        account = value["account"]
        if not isinstance(account, str) or account not in clients:
            raise ValueError("inbox card account is not enrolled")
        return account

    assigned, failed = [], 0
    for row in rows:
        if row["assignee"] == "inbox-sol":
            try:
                account = due(row)
                if account: assigned.append((row["id"], account))
            except (TypeError, ValueError, AttributeError, KeyError):
                failed += 1
    if any(sum(account == selected for _, selected in assigned) > daily.MAX_MESSAGES for account in clients):
        raise OverflowError("inbox pending draft cards exceed per-account cap")
    staged_count = 0
    for index, (task_id, account) in enumerate(assigned):
        if deadline is not None and time.monotonic() >= deadline:
            failed += len(assigned) - index
            break
        try:
            controller.process(board, ledger, task_id, clients[account][0], hermes_bin, workdir)
        except Exception:
            failed += 1
        else:
            staged_count += 1
    rows = board.execute(
        "SELECT id,body FROM tasks WHERE status='blocked' AND assignee IS NULL "
        "AND created_by='inbox-intake' ORDER BY created_at,id"
    ).fetchall()
    staged = []
    for row in rows:
        try:
            account = due(row)
            if account and json.loads(row["body"]).get("intent_id") == row["id"]:
                staged.append((row["id"], account))
        except (TypeError, ValueError, AttributeError, KeyError):
            failed += 1
    if any(len({task for task, selected in assigned + staged if selected == account}) > daily.MAX_MESSAGES
           for account in clients):
        raise OverflowError("inbox pending draft cards exceed per-account cap")
    published = 0
    for index, (task_id, account) in enumerate(staged):
        if deadline is not None and time.monotonic() >= deadline:
            failed += len(staged) - index
            break
        try:
            publisher.create(board, ledger, task_id, *clients[account])
        except Exception:
            failed += 1
        else:
            published += 1
    return staged_count, published, failed


def run_today(root: Path, hermes_bin: str, now: datetime):
    with daily.lock_manifest(Path(root) / "runner") as acquired:
        if not acquired:
            raise RunnerBusy("inbox Draft runner is already active")
        return _run_locked(root, hermes_bin, now)


def _run_locked(root: Path, hermes_bin: str, now: datetime):
    from hermes_cli import kanban_db_connect as board_db
    zone = Path('/etc/localtime').resolve()
    if (zone.parent.name, zone.name) != ('America', 'New_York'):
        raise ValueError('daily inbox scheduler requires America/New_York host timezone')
    root = Path(root)
    manifest = root / "manifest.sqlite"
    with closing(daily.open_manifest(manifest)) as state:
        bound = state.execute("SELECT account FROM account_binding WHERE id=1").fetchone()
        first = state.execute("SELECT MIN(day) FROM days").fetchone()[0]
    if not bound or not first:
        raise ValueError("inbox account or first-run date is not enrolled")
    config = yaml.safe_load((Path.home() / ".hermes/config.yaml").read_text()) or {}
    if (config.get("kanban") or {}).get("default_assignee"):
        raise ValueError("unassigned inbox review cards would be auto-dispatched")
    day = reader.previous_day(now)[0]
    accounts = [(bound["account"], manifest, date.fromisoformat(first), root)]
    extra = root / SECOND_ACCOUNT
    enabled = extra / "enabled"
    errors = 0
    try:
        if extra.is_symlink() or enabled.is_symlink():
            raise ValueError("second inbox enrollment must not be linked")
        if enabled.exists():
            marker = enabled.lstat()
            if not stat.S_ISREG(marker.st_mode) or marker.st_uid != os.getuid() or stat.S_IMODE(marker.st_mode) != 0o600:
                raise ValueError("second inbox enrollment must be owner-only")
            extra_manifest = extra / "manifest.sqlite"
            with closing(daily.open_manifest(extra_manifest)) as state:
                enrolled = state.execute("SELECT account FROM account_binding WHERE id=1").fetchone()
                first_extra = state.execute("SELECT MIN(day) FROM days").fetchone()[0]
            if (enrolled and enrolled["account"] != SECOND_ACCOUNT) or bool(enrolled) != bool(first_extra):
                raise ValueError("second inbox account or first-run date is inconsistent")
            accounts.append((SECOND_ACCOUNT, extra_manifest, date.fromisoformat(first_extra) if first_extra else day, extra))
    except Exception:
        errors += 1
    clients = {}
    for account, _, _, directory in accounts:
        try:
            read_client = gmail.connect(directory / "gmail-read-token.json")
            draft_client = draft_api.connect(directory / "gmail-draft-token.json")
            if (read_client.profile()["emailAddress"].casefold() != account
                    or draft_client.profile()["emailAddress"].casefold() != account):
                raise ValueError("inbox reader or draft credential belongs to another account")
            clients[account] = read_client, draft_client
        except Exception:
            if account != SECOND_ACCOUNT:
                raise
            errors += 1
    deadline = time.monotonic() + MAX_RUN_SECONDS
    with board_db.connect_closing(board="inbox-replies") as board:
        if publisher.recover_started(root / "drafts.sqlite"):
            daily.kanban_notice(hermes_bin, day.isoformat(), "draft-reconcile")
        with tempfile.TemporaryDirectory(prefix="inbox-work-", dir=root) as workspace:
            processed = 0
            for account, account_manifest, since, _ in accounts:
                if account not in clients:
                    continue
                try:
                    processed += pipeline.run_once(account_manifest, clients[account][0], hermes_bin, account,
                                                   since, now, workdir=Path(workspace), deadline=deadline)
                except Exception:
                    errors += 1
            try:
                staged, created, failed = run_cards(board, root / "drafts.sqlite", clients,
                                                    hermes_bin, Path(workspace), day, deadline=deadline)
            except Exception:
                daily.kanban_notice(hermes_bin, day.isoformat(), "draft-action-needed")
                raise
            if failed:
                daily.kanban_notice(hermes_bin, day.isoformat(), "draft-action-needed")
            if errors or failed:
                raise RuntimeError("inbox account intake or draft work incomplete")
    return processed, staged, created


def _status(root, day, status, counts=None):
    path = Path(root) / "last-run.json"
    daily._private_parent(path)
    temporary = path.with_name(".last-run-" + secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"day": day, "status": status, "counts": counts,
                       "checked_at": datetime.now(timezone.utc).isoformat()}, output)
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
        dirfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(dirfd)
        finally: os.close(dirfd)
    finally:
        if temporary.exists(): temporary.unlink()


def main(root, hermes_bin, now):
    day = reader.previous_day(now)[0].isoformat()
    try:
        result = run_today(root, hermes_bin, now)
        _status(root, day, "complete", result)
    except RunnerBusy:
        print("Inbox Draft run skipped: another run is active")
        return None
    except Exception as error:
        try: _status(root, day, "failed")
        except Exception: pass
        try: daily.kanban_notice(hermes_bin, day, "runner-error")
        except Exception:
            try:
                subprocess.run(["/usr/bin/osascript", "-e",
                    'display notification "Inbox Draft run failed; check local status" with title "Hermes inbox"'],
                    capture_output=True, timeout=8)
            except Exception: pass
        raise SystemExit("Inbox Draft run failed: " + type(error).__name__) from None
    print("Inbox Draft run: triaged=%d staged=%d drafts_checked=%d" % result)
    return result


if __name__ == "__main__":
    main(Path.home() / ".config/ai-pr-automation/inbox",
         str(Path.home() / ".hermes/runtime-v0.21.5/venv/bin/hermes"),
         datetime.now(timezone.utc))
