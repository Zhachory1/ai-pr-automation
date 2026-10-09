"""Run tool-free Sol on one admitted blocked card and stage its reply locally."""

import hashlib
import json
from pathlib import Path
import re

import hermes_inbox_sol as sol
import hermes_inbox_stage_card as staging


KEY = re.compile(r"inbox-message-[0-9a-f]{64}\Z")


def process(board, drafts_path: Path, task_id: str, gmail, hermes_bin: str,
            workdir: Path, *, ops=None):
    card = board.execute(
        "SELECT body,status,assignee,claim_lock,created_by,idempotency_key FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    if (not card or card["status"] != "blocked" or card["assignee"] != "inbox-sol"
            or card["claim_lock"] is not None or card["created_by"] != "inbox-intake"
            or not isinstance(card["idempotency_key"], str) or not KEY.fullmatch(card["idempotency_key"])):
        raise ValueError("card is not an unclaimed inbox Sol task")
    source = json.loads(card["body"])
    if source.get("intent_id") == task_id:
        message_id = source.get("source_message_id")
        expected = "inbox-message-" + hashlib.sha256(
            f"{source['account'].casefold()}\0{message_id}".encode()
        ).hexdigest()
        if card["idempotency_key"] != expected:
            raise ValueError("inbox staged card key changed")
        return staging.stage(board, drafts_path, task_id, gmail, source["draft"], ops=ops)
    if set(source) != {"account", "message_id", "thread_id", "route", "source_day"} \
            or source["route"] not in ("job", "help"):
        raise ValueError("inbox source card changed")
    expected = "inbox-message-" + hashlib.sha256(
        f"{source['account'].casefold()}\0{source['message_id']}".encode()
    ).hexdigest()
    if card["idempotency_key"] != expected:
        raise ValueError("inbox source key changed")
    if gmail.profile()["emailAddress"].casefold() != source["account"].casefold():
        raise ValueError("inbox account changed before thread read")
    thread = gmail.get_thread(source["thread_id"])
    latest = max(thread, key=lambda item: (int(item["internalDate"]), item["id"]))
    if latest["id"] != source["message_id"]:
        raise ValueError("inbox source changed before drafting")
    draft_body = sol.draft(hermes_bin, workdir, source["route"], thread, sol.read_voice())
    return staging.stage(board, drafts_path, task_id, gmail, draft_body, ops=ops)
