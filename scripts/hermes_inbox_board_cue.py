"""Record a staged reply on an unchanged, blocked inbox card."""

import hashlib
import json
import re
import sqlite3


KEY = re.compile(r"inbox-message-[0-9a-f]{64}\Z")


def stage(conn: sqlite3.Connection, task_id: str, body_sha256: str) -> int:
    """Record reviewed bytes only for an admitted, blocked, unassigned inbox card."""
    with conn:
        task = conn.execute(
            "SELECT body, created_by, idempotency_key FROM tasks WHERE id = ? AND status = 'blocked' "
            "AND assignee IS NULL AND claim_lock IS NULL", (task_id,)
        ).fetchone()
        if (not task or task["created_by"] != "inbox-intake"
                or not isinstance(task["idempotency_key"], str)
                or not KEY.fullmatch(task["idempotency_key"])
                or hashlib.sha256((task["body"] or "").encode()).hexdigest() != body_sha256):
            raise ValueError("review card is not an unchanged admitted inbox card")
        return conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?, 'inbox_staged', ?, unixepoch())",
            (task_id, json.dumps({"body_sha256": body_sha256})),
        ).lastrowid
