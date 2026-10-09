"""Admit a triaged Gmail message to Hermes Kanban without Gmail or model credentials."""

from datetime import date
import hashlib
import json
import re
import subprocess


BOARD = "inbox-replies"
PROFILE = "inbox-sol"
ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
TASK_ID = re.compile(r"t_[0-9a-f]{8}\Z")
KEY = re.compile(r"inbox-message-[0-9a-f]{64}\Z")


def parse_route(output: str) -> str:
    """Reject model prose, extra fields, duplicate keys, and non-routing actions."""
    if not isinstance(output, str) or len(output) > 512:
        raise ValueError("invalid Luna classification")
    try:
        value = json.loads(output, object_pairs_hook=lambda pairs: pairs)
    except json.JSONDecodeError as error:
        raise ValueError("invalid Luna classification") from error
    if value not in ([('route', 'job')], [('route', 'help')], [('route', 'skip')]):
        raise ValueError("invalid Luna classification")
    return value[0][1]


def _cli(binary, *args, json_output=False):
    command = [binary, "kanban", "--board", BOARD, *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Hermes inbox Kanban operation failed")
    if json_output:
        return json.loads(result.stdout)
    return None


def admit(binary, account, classify, day, message, key):
    """Use Luna's structured route; return admitted/skipped for the daily seen ledger."""
    route = classify(message)
    if route == "skip":
        return "skipped"
    if route not in ("job", "help"):
        raise ValueError("Luna route must be job, help, or skip")
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("inbox source date must be an ISO day")
    message_id, thread_id = message["id"], message["threadId"]
    if (not ID.fullmatch(message_id) or not ID.fullmatch(thread_id)
            or not KEY.fullmatch(key)
            or key != "inbox-message-" + hashlib.sha256(f"{account.casefold()}\0{message_id}".encode()).hexdigest()):
        raise ValueError("inbox source identity differs from daily manifest")
    body = json.dumps({"account": account.casefold(), "message_id": message_id,
                       "thread_id": thread_id, "route": route, "source_day": day},
                      sort_keys=True, separators=(",", ":"))
    title = "Inbox job request" if route == "job" else "Inbox help request"
    task = _cli(binary, "create", title, "--body", body, "--assignee", PROFILE,
                "--initial-status", "blocked", "--idempotency-key", key,
                "--created-by", "inbox-intake", "--max-runtime", "600", "--max-retries", "1",
                "--json", json_output=True)
    task_id = task.get("id") if isinstance(task, dict) else None
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise RuntimeError("Hermes inbox card identity missing")
    current = _cli(binary, "show", task_id, "--json", json_output=True)
    if not isinstance(current, dict) or not isinstance(current.get("task"), dict) or current["task"].get("id") != task_id:
        raise RuntimeError("Hermes inbox card readback failed")
    if current["task"].get("created_by") != "inbox-intake":
        raise RuntimeError("inbox card was not created by intake")
    status = current["task"].get("status")
    if status == "blocked" and [e.get("kind") for e in current.get("events", [])] == ["created", "blocked"] and current.get("runs") == []:
        # The first keyed card's route wins; replay must not create a second card if Luna varies.
        try:
            original = json.loads(current["task"].get("body", ""))
        except (TypeError, ValueError) as error:
            raise RuntimeError("inbox card binding changed before dispatch") from error
        if (not isinstance(original, dict) or set(original) != {"account", "message_id", "thread_id", "route", "source_day"}
                or any(original[field] != value for field, value in
                       {"account": account.casefold(), "message_id": message_id,
                        "thread_id": thread_id, "source_day": day}.items())
                or original["route"] not in ("job", "help")
                or current["task"].get("assignee") != PROFILE):
            raise RuntimeError("inbox card binding changed before dispatch")
        fresh = _cli(binary, "show", task_id, "--json", json_output=True)
        if (fresh.get("task") != current["task"] or fresh.get("events") != current.get("events")
                or fresh.get("runs") != current.get("runs")):
            raise RuntimeError("inbox card changed before dispatch")
    elif status != "blocked":
        raise RuntimeError("inbox card left blocked intake; reconcile before retry")
    return "admitted"
