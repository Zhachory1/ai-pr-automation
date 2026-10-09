"""Create one Gmail Draft for a staged Kanban reply; never send email."""

from contextlib import closing
from email.parser import BytesParser
from email.policy import SMTP
import hashlib
import json

import hermes_inbox_draft as ledger


HEADERS = ("From", "To", "Subject", "In-Reply-To", "References", "Message-ID")


def _card(board, task_id):
    task = board.execute(
        "SELECT body,status,assignee,claim_lock,created_by FROM tasks WHERE id=?", (task_id,),
    ).fetchone()
    if (not task or task["status"] != "blocked" or task["assignee"] is not None
            or task["claim_lock"] is not None or task["created_by"] != "inbox-intake"):
        raise ValueError("Gmail draft requires a blocked, unassigned inbox review card")
    preview = json.loads(task["body"])
    if preview.get("intent_id") != task_id:
        raise ValueError("card is not an inbox reply preview")
    digest = hashlib.sha256(task["body"].encode()).hexdigest()
    marker = board.execute(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='inbox_staged' ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if not marker or json.loads(marker["payload"]).get("body_sha256") != digest:
        raise ValueError("card was edited after staging")
    return task["body"], preview


def _verified_receipt(receipt, draft_id, thread_id, mime):
    if (receipt.get("id") != draft_id or receipt.get("message", {}).get("threadId") != thread_id
            or "DRAFT" not in receipt.get("message", {}).get("labelIds", [])):
        raise ValueError("Gmail Draft receipt is ambiguous")
    parsed = BytesParser(policy=SMTP).parsebytes(mime)
    headers = receipt["message"]["headers"]
    for name in HEADERS:
        values = [header["value"] for header in headers if header["name"].lower() == name.lower()]
        if len(values) != 1 or (name == "Message-ID" and not ledger.MESSAGE_ID.fullmatch(values[0])) \
                or (name != "Message-ID" and values[0] != str(parsed[name])):
            raise ValueError("Gmail Draft differs from staged reply")


def recover_started(path):
    """Call only after acquiring the inbox runner lock; never replay Gmail creation."""
    with closing(ledger._private_db(path)) as conn:
        with conn:
            return conn.execute("UPDATE gmail_drafts SET status='reconcile' WHERE status='started'").rowcount


def _settle(path, task_id, status, draft_id=None):
    with closing(ledger._private_db(path)) as conn:
        with conn:
            count = conn.execute(
                "UPDATE gmail_drafts SET status=?,draft_id=COALESCE(?,draft_id) "
                "WHERE task_id=? AND status='started'",
                (status, draft_id, task_id),
            ).rowcount
        if count != 1:
            raise RuntimeError("draft creation could not be settled")


def _remember_id(path, task_id, draft_id):
    with closing(ledger._private_db(path)) as conn:
        with conn:
            conn.execute("UPDATE gmail_drafts SET draft_id=? WHERE task_id=? AND status='started'",
                         (draft_id, task_id))


def _board_receipt(board, task_id, draft_id, thread_id, version):
    payload = json.dumps({"draft_id": draft_id, "thread_id": thread_id, "version": version},
                         sort_keys=True, separators=(",", ":"))
    with board:
        exists = board.execute(
            "SELECT 1 FROM task_events WHERE task_id=? AND kind='gmail_draft_created' AND payload=?",
            (task_id, payload),
        ).fetchone()
        if not exists:
            board.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) "
                "VALUES (?,'gmail_draft_created',?,unixepoch())", (task_id, payload),
            )


def reconcile_existing(board, ledger_path, task_id, draft_id, drafts):
    """Operator-initiated readback of a known draft ID; never create another draft."""
    _body, preview = _card(board, task_id)
    with closing(ledger._private_db(ledger_path)) as conn:
        row = conn.execute("SELECT * FROM drafts WHERE task_id=?", (task_id,)).fetchone()
        attempt = conn.execute("SELECT * FROM gmail_drafts WHERE task_id=?", (task_id,)).fetchone()
        if (not row or not attempt or attempt["status"] != "reconcile"
                or attempt["draft_id"] != draft_id or attempt["version"] != row["version"]
                or attempt["digest"] != row["digest"] or preview.get("digest") != row["digest"]
                or preview.get("to") != row["recipient"] or preview.get("thread_id") != row["thread_id"]):
            raise ValueError("uncertain Gmail Draft differs from staged review card")
        mime, thread_id, version, account = row["mime"], row["thread_id"], row["version"], row["account"]
    if drafts.profile()["emailAddress"].casefold() != account.casefold():
        raise ValueError("draft account mismatch")
    _verified_receipt(drafts.get(draft_id), draft_id, thread_id, mime)
    with closing(ledger._private_db(ledger_path)) as conn:
        with conn:
            changed = conn.execute("UPDATE gmail_drafts SET status='created' "
                                   "WHERE task_id=? AND draft_id=? AND status='reconcile'",
                                   (task_id, draft_id)).rowcount
        if changed != 1:
            raise ValueError("Gmail Draft reconciliation changed concurrently")
    _board_receipt(board, task_id, draft_id, thread_id, version)
    return draft_id


def create(board, ledger_path, task_id, reader, drafts):
    body, preview = _card(board, task_id)
    with closing(ledger._private_db(ledger_path)) as conn:
        row = conn.execute("SELECT * FROM drafts WHERE task_id=?", (task_id,)).fetchone()
        if (not row or any(preview.get(key) != row[column] for key, column in (
            ("account", "account"), ("thread_id", "thread_id"),
            ("source_message_id", "source_id"), ("to", "recipient"),
            ("version", "version"), ("digest", "digest"), ("draft", "body"),
        )) or hashlib.sha256(row["mime"]).hexdigest() != row["digest"]):
            raise ValueError("Gmail draft target differs from reviewed card")
        existing = conn.execute("SELECT * FROM gmail_drafts WHERE task_id=?", (task_id,)).fetchone()
        if existing:
            if existing["status"] == "created" and existing["version"] == row["version"] \
                    and existing["digest"] == row["digest"]:
                _board_receipt(board, task_id, existing["draft_id"], row["thread_id"], row["version"])
                return existing["draft_id"]
            raise ValueError("Gmail draft outcome requires manual reconciliation")
        account, thread_id, source_id = row["account"], row["thread_id"], row["source_id"]
        fingerprint, mime, digest, version = row["fingerprint"], row["mime"], row["digest"], row["version"]
    if (reader.profile()["emailAddress"].casefold() != account.casefold()
            or drafts.profile()["emailAddress"].casefold() != account.casefold()):
        raise ValueError("Gmail Draft and reader account differ from staged account")
    thread = reader.get_thread(thread_id)
    head = max(thread, key=lambda item: (int(item["internalDate"]), item["id"]))
    current = hashlib.sha256(f"{thread_id}\0{head['id']}\0{head['internalDate']}".encode()).hexdigest()
    if head["id"] != source_id or current != fingerprint:
        raise ValueError("Gmail thread advanced since draft staging")
    if _card(board, task_id)[0] != body:
        raise ValueError("review card changed before Gmail draft creation")
    with closing(ledger._private_db(ledger_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM gmail_drafts WHERE task_id=?", (task_id,)).fetchone():
            raise ValueError("Gmail draft was claimed concurrently")
        conn.execute("INSERT INTO gmail_drafts(task_id,version,digest,status) VALUES (?,?,?,'started')",
                     (task_id, version, digest))
        conn.commit()
    try:
        if _card(board, task_id)[0] != body:
            raise ValueError("review card changed after draft claim")
    except Exception:
        with closing(ledger._private_db(ledger_path)) as conn:
            with conn:
                conn.execute("DELETE FROM gmail_drafts WHERE task_id=? AND status='started' AND draft_id IS NULL",
                             (task_id,))
        raise
    try:
        draft_id = drafts.create(mime, thread_id)
        _remember_id(ledger_path, task_id, draft_id)
        _verified_receipt(drafts.get(draft_id), draft_id, thread_id, mime)
        _settle(ledger_path, task_id, "created", draft_id)
    except Exception:
        _settle(ledger_path, task_id, "reconcile")
        raise
    _board_receipt(board, task_id, draft_id, thread_id, version)
    return draft_id
