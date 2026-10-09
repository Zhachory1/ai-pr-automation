"""Stage a tool-free Sol reply on its already-blocked Kanban card."""

import hashlib
import json
import re

import hermes_inbox_board_cue as cue
import hermes_inbox_draft as drafts


KEY = re.compile(r"inbox-message-[0-9a-f]{64}\Z")


def stage(conn, drafts_path, task_id, gmail, body, *, ops=None, restage=False):
    if ops is None:
        from hermes_cli import kanban_db as ops
    task = conn.execute(
        "SELECT body,status,assignee,claim_lock,created_by,idempotency_key FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if (not task or task["status"] != "blocked" or task["claim_lock"] is not None
            or task["created_by"] != "inbox-intake" or not isinstance(task["idempotency_key"], str)
            or not KEY.fullmatch(task["idempotency_key"])):
        raise ValueError("card is not a blocked inbox intake task")
    card = json.loads(task["body"])
    already_staged = card.get("intent_id") == task_id
    if already_staged:
        if (task["assignee"] not in (None, "inbox-sol") or card.get("draft") != body
                or set(card) != {"account", "intent_id", "source_message_id", "thread_id",
                                 "source_day", "version", "to", "digest", "draft"}):
            raise ValueError("staged card changed; explicit restage required")
        account, message_id, thread_id, source_day = (card["account"], card["source_message_id"],
                                                   card["thread_id"], card["source_day"])
        previous = drafts.lookup(drafts_path, task_id)
        if not previous or any(card[field] != previous[column] for field, column in (
            ("account", "account"), ("source_message_id", "source_id"),
            ("thread_id", "thread_id"), ("to", "recipient"),
            ("digest", "digest"), ("version", "version"),
        )):
            raise ValueError("staged card target or version changed")
        if previous["body"] != body and (not restage or task["assignee"] is not None):
            raise ValueError("draft edit requires explicit restage on blocked unassigned card")
    else:
        if task["assignee"] != "inbox-sol" or set(card) != {
            "account", "message_id", "thread_id", "route", "source_day"
        } or card["route"] not in ("job", "help"):
            raise ValueError("intake card identity changed")
        account, message_id, thread_id, source_day = (card["account"], card["message_id"],
                                                   card["thread_id"], card["source_day"])
    expected = "inbox-message-" + hashlib.sha256(f"{account.casefold()}\0{message_id}".encode()).hexdigest()
    if task["idempotency_key"] != expected:
        raise ValueError("intake card message key changed")
    thread = gmail.get_thread(thread_id)
    head = max(thread, key=lambda item: (int(item["internalDate"]), item["id"]))
    if head["id"] != message_id:
        raise ValueError("inbox thread advanced after admission")
    draft = drafts.stage(drafts_path, task_id, gmail, account, thread_id, thread, body,
                         restage=restage)
    preview = json.dumps({"account": account, "intent_id": task_id, "source_message_id": message_id,
                          "thread_id": thread_id, "source_day": source_day,
                          "version": draft["version"], "to": draft["recipient"],
                          "digest": draft["digest"], "draft": body}, sort_keys=True, separators=(",", ":"))
    before = conn.execute(
        "SELECT body,status,assignee,claim_lock,created_by,idempotency_key FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if not before or any(before[key] != task[key] for key in task.keys()):
        raise ValueError("inbox card changed during draft preparation")
    if already_staged and task["body"] != preview and not restage:
        raise ValueError("staged review card was edited; explicit restage required")
    if task["body"] != preview and not ops.edit_task(conn, task_id, body=preview):
        raise RuntimeError("inbox card draft preview could not be saved")
    after = conn.execute(
        "SELECT body,status,assignee,claim_lock FROM tasks WHERE id = ?", (task_id,),
    ).fetchone()
    if (not after or after["body"] != preview or after["status"] != "blocked"
            or after["claim_lock"] is not None or after["assignee"] != task["assignee"]):
        raise ValueError("inbox card changed before unassignment")
    if task["assignee"] is not None and not ops.assign_task(conn, task_id, None):
        raise RuntimeError("inbox card could not be unassigned")
    latest = conn.execute(
        "SELECT id,payload FROM task_events WHERE task_id = ? AND kind = 'inbox_staged' ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    body_hash = hashlib.sha256(preview.encode()).hexdigest()
    if latest and json.loads(latest["payload"]).get("body_sha256") == body_hash:
        draft["stage_event"] = latest["id"]
    else:
        draft["stage_event"] = cue.stage(conn, task_id, body_hash)
    return draft
