"""Stage one exact plain-text reply in a private local SQLite ledger; never send."""

from email.message import EmailMessage
from email.policy import SMTP
from email.utils import getaddresses, make_msgid
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat


ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\Z")
SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MESSAGE_ID = re.compile(r"<[^<>\s@]+@[^<>\s@]+>\Z")
TASK_ID = re.compile(r"t_[0-9a-f]{8}\Z")


def _header(headers, name):
    values = [h["value"] for h in headers if h["name"].lower() == name.lower()]
    if len(values) != 1 or not isinstance(values[0], str) or len(values[0]) > 512 \
            or any(char in values[0] for char in "\r\n\x00"):
        raise ValueError("missing, repeated, or unsafe mail header: " + name)
    return values[0]


def _optional_header(headers, name):
    values = [h["value"] for h in headers if h["name"].lower() == name.lower()]
    if not values:
        return None
    return _header(headers, name)


def _recipient(headers):
    raw = _optional_header(headers, "Reply-To") or _header(headers, "From")
    addresses = getaddresses([raw])
    if len(addresses) != 1 or not ADDRESS.fullmatch(addresses[0][1]):
        raise ValueError("reply recipient must be exactly one valid address")
    return addresses[0][1]


def _private_db(path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if path.parent.is_symlink() or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ValueError("draft directory must be owner-only")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = None
    if fd is not None:
        os.close(fd)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("draft ledger must be owner-only")
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("""CREATE TABLE IF NOT EXISTS drafts (
        task_id TEXT PRIMARY KEY, account TEXT NOT NULL, thread_id TEXT NOT NULL,
        source_id TEXT NOT NULL, fingerprint TEXT NOT NULL, recipient TEXT NOT NULL,
        body TEXT NOT NULL, mime BLOB NOT NULL, digest TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS gmail_drafts (
        task_id TEXT PRIMARY KEY, version INTEGER NOT NULL, digest TEXT NOT NULL,
        status TEXT NOT NULL, draft_id TEXT
    )""")
    return conn


def lookup(path, task_id):
    conn = _private_db(path)
    try:
        return conn.execute("SELECT account,thread_id,source_id,recipient,body,digest,version "
                            "FROM drafts WHERE task_id = ?", (task_id,)).fetchone()
    finally:
        conn.close()


def stage(path, task_id, gmail, account, thread_id, thread, body, *, restage=False):
    if gmail.profile()["emailAddress"].casefold() != account.casefold():
        raise ValueError("draft account does not match authenticated Gmail account")
    if (not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id)
            or not isinstance(account, str) or not ADDRESS.fullmatch(account)
            or not isinstance(thread_id, str) or not SOURCE_ID.fullmatch(thread_id)
            or not thread or not isinstance(body, str) or not body.strip() or len(body) > 6000
            or "\x00" in body):
        raise ValueError("invalid draft target or body")
    latest = max(thread, key=lambda message: (int(message["internalDate"]), message["id"]))
    source_id = latest["id"]
    if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
        raise ValueError("invalid source message ID")
    headers = latest["headers"]
    recipient = _recipient(headers)
    if recipient.casefold() == account.casefold():
        raise ValueError("self-addressed inbox replies require manual handling")
    subject = _header(headers, "Subject")
    source_message_id = _header(headers, "Message-ID")
    if not MESSAGE_ID.fullmatch(source_message_id):
        raise ValueError("source Message-ID is invalid")
    previous = _optional_header(headers, "References")
    if previous and (len(previous) > 512 or any(char in previous for char in "\r\n\x00")):
        raise ValueError("unsafe reply references")
    fingerprint = hashlib.sha256(f"{thread_id}\0{source_id}\0{latest['internalDate']}".encode()).hexdigest()
    conn = _private_db(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM drafts WHERE task_id = ?", (task_id,)).fetchone()
        if existing and any(existing[key] != value for key, value in (
            ("account", account), ("thread_id", thread_id), ("source_id", source_id),
            ("fingerprint", fingerprint), ("recipient", recipient),
        )):
            raise ValueError("draft source or recipient changed")
        if existing and existing["body"] == body:
            result = {"recipient": existing["recipient"], "source_id": existing["source_id"],
                      "mime": existing["mime"], "digest": existing["digest"],
                      "version": existing["version"]}
        else:
            if existing and (not restage or conn.execute(
                "SELECT 1 FROM gmail_drafts WHERE task_id = ?", (task_id,)
            ).fetchone()):
                raise ValueError("draft changed after Gmail draft attempt or without explicit restage")
            message = EmailMessage(policy=SMTP)
            message["From"] = account
            message["To"] = recipient
            message["Subject"] = subject if subject.lower().startswith("re:") else "Re: " + subject
            message["In-Reply-To"] = source_message_id
            message["References"] = ((previous + " ") if previous else "") + source_message_id
            message["Message-ID"] = make_msgid(domain=account.split("@", 1)[1])
            message.set_content(body, subtype="plain", charset="utf-8")
            mime = message.as_bytes()
            digest = hashlib.sha256(mime).hexdigest()
            if existing:
                version = existing["version"] + 1
                conn.execute("UPDATE drafts SET body = ?, mime = ?, digest = ?, version = ? WHERE task_id = ?",
                             (body, mime, digest, version, task_id))
            else:
                version = 1
                conn.execute("INSERT INTO drafts(task_id,account,thread_id,source_id,fingerprint,recipient,body,mime,digest) "
                             "VALUES (?,?,?,?,?,?,?,?,?)",
                             (task_id, account, thread_id, source_id, fingerprint, recipient, body, mime, digest))
            result = {"recipient": recipient, "source_id": source_id, "mime": mime,
                      "digest": digest, "version": version}
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
