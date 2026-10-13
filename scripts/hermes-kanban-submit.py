#!/usr/bin/env python3
"""Submit one discovered PR to the host's authenticated Kanban admission endpoint."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from hermes_direct_pr_journal import identity
from hermes_run_request import NoRedirect


def submit(kind, repo, number, title, head, feedback=None):
    if kind not in {"pr-review", "pr-maintain"} or (kind == "pr-review") != (feedback is None):
        raise ValueError("invalid submission kind")
    operation = identity(kind, repo, number, head, feedback)
    url = os.environ.get("HERMES_KANBAN_INGRESS_URL", "http://host.docker.internal:8767")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "host.docker.internal"}
            or parsed.port != 8767 or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.username or parsed.password):
        raise ValueError("Kanban ingress must be local")
    key = Path(os.environ.get("HERMES_KANBAN_INGRESS_KEY_FILE", "")).read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("invalid Kanban ingress key")
    payload = {"repo": repo, "number": number, "url": f"https://github.com/{repo}/pull/{number}",
               "title": title, "head_sha": head}
    if feedback is not None:
        payload["feedback_digest"] = feedback
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    path = "/v1/pr-tasks/" + kind
    timestamp = str(int(time.time()))
    signed = b"POST\n" + path.encode() + b"\n" + timestamp.encode() + b"\n" + data
    signature = hmac.new(bytes.fromhex(key), signed, hashlib.sha256).hexdigest()
    request = urllib.request.Request(url.rstrip("/") + path, data=data, method="POST",
                                     headers={"X-Hermes-Timestamp": timestamp,
                                              "X-Hermes-Signature": signature, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=130) as response:
            if response.status != 200:
                raise ValueError("Kanban ingress admission failed")
            raw = response.read(4097)
    except urllib.error.HTTPError as error:
        raw_error = error.read(4097) if error.code in {400, 503} else b""
        try:
            problem = json.loads(raw_error) if len(raw_error) <= 4096 else None
        except (ValueError, UnicodeError):
            problem = None
        reason = problem["error"] if type(problem) is dict and set(problem) == {"error"} else None
        if ((error.code == 400 and isinstance(reason, str) and
             (reason == "unresolved create outcome" or re.fullmatch(r"(?:unresolved create outcome|prior maintenance request incomplete) for pr-maintain-[0-9a-f]{64}", reason)))
                or (error.code == 503 and reason == "ingress busy")):
            raise ValueError(f"Kanban {operation['operation_id']}: {reason}; inspect admission before retry") from None
        raise ValueError("Kanban ingress unavailable or rejected the request") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ValueError("Kanban ingress unavailable or rejected the request") from error
    if len(raw) > 4096:
        raise ValueError("Kanban ingress response too large")
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {"kind", "board", "operation_id", "task_id", "status"}
            or value["kind"] != kind or value["board"] != kind
            or not isinstance(value["operation_id"], str)
            or (kind == "pr-review" and value["operation_id"] != operation["operation_id"])
            or (kind == "pr-maintain" and (not re.fullmatch(r"pr-maintain-[0-9a-f]{64}", value["operation_id"])
                or (value["status"] in {"capped", "deferred"} and value["operation_id"] != operation["operation_id"])))
            or value["status"] not in {"ready", "active", "review", "done", "capped", "deferred", "superseded"}
            or (kind == "pr-review" and value["status"] == "capped"
                or kind == "pr-maintain" and value["status"] == "superseded")
            or ((value["status"] in {"capped", "deferred", "superseded"}) != (value["task_id"] is None))
            or (value["task_id"] is not None and not re.fullmatch(r"t_[0-9a-f]{8}", value["task_id"]))):
        raise ValueError("invalid Kanban ingress result")
    return value


def main():
    kind, repo, number, title, head, *remaining = sys.argv[1:]
    if len(remaining) != (1 if kind == "pr-maintain" else 0):
        raise ValueError("usage: hermes-kanban-submit KIND REPO NUMBER TITLE HEAD [FEEDBACK_DIGEST]")
    value = submit(kind, repo, int(number), title, head, remaining[0] if remaining else None)
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError) as error:
        raise SystemExit(f"Kanban submission failed: {error}") from None
