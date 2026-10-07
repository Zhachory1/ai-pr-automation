#!/usr/bin/env python3
"""Authenticated loopback ingress for Compose PR discovery into the host's Kanban CLI."""
import argparse
from collections import namedtuple
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_direct_pr_journal import identity


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
AUTHORITY = load_module("hermes_authority", HERE / "hermes-authority.py")
ENQUEUE = load_module("hermes_pr_enqueue", HERE / "hermes-pr-kanban-enqueue.py")
Config = namedtuple("Config", "work authority home hermes_bin keys")
LOCK = threading.Lock()
STATE = None
KINDS = {"pr-review", "pr-maintain"}


def validate(payload, kind):
    fields = {"repo", "number", "url", "title", "head_sha"} | ({"feedback_digest"} if kind == "pr-maintain" else set())
    if type(payload) is not dict or set(payload) != fields or type(payload["number"]) is not int or payload["number"] < 1:
        raise ValueError("invalid request fields")
    if type(payload["title"]) is not str or not 0 < len(payload["title"]) <= 256:
        raise ValueError("invalid PR title")
    operation = identity(kind, payload["repo"], payload["number"], payload["head_sha"], payload.get("feedback_digest"))
    if payload["url"] != f"https://github.com/{payload['repo']}/pull/{payload['number']}":
        raise ValueError("invalid PR URL")
    return operation


def round_request(config, payload):
    repo, number, digest = payload["repo"].lower(), payload["number"], payload["feedback_digest"]
    seen = {}
    entries = [entry for entry in config.work.iterdir() if re.fullmatch(r"pr-maintain-[0-9a-f]{64}", entry.name)]
    if len(entries) > 1000:
        raise ValueError("too much maintenance history")
    for entry in entries:
        path = entry / "request.json"
        if not path.exists():
            continue
        raw = ENQUEUE.read_immutable(path)
        previous = json.loads(raw)
        if (raw != ENQUEUE.canonical(previous) or type(previous) is not dict
                or set(previous) != {"operation_id", "repo", "number", "url", "title", "head_sha", "feedback_digest", "round"}
                or type(previous["round"]) is not int or previous["round"] not in range(1, 4)
                or validate({key: previous[key] for key in ("repo", "number", "url", "title", "head_sha", "feedback_digest")}, "pr-maintain")["operation_id"] != previous["operation_id"]
                or entry.name != previous["operation_id"]):
            raise ValueError("invalid maintenance history")
        if previous["repo"].lower() == repo and previous["number"] == number:
            prior_digest = previous["feedback_digest"]
            if prior_digest in seen and seen[prior_digest]["operation_id"] != previous["operation_id"]:
                raise ValueError("conflicting maintenance history")
            seen[prior_digest] = previous
            if len(seen) > 3:
                raise ValueError("maintenance round cap exceeded")
    if digest in seen:
        return seen[digest]
    if len(seen) == 3:
        return None
    operation = validate(payload, "pr-maintain")
    return {"operation_id": operation["operation_id"], **payload, "round": len(seen) + 1}


def invoke(config, kind, payload):
    command = [sys.executable, "-B", str(HERE / "hermes-pr-kanban-enqueue.py"), "--kind", kind,
               "--hermes-home", str(config.home), "--hermes-bin", str(config.hermes_bin),
               "--workspace-root", str(config.work)]
    result = subprocess.run(command, input=ENQUEUE.canonical(payload), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=120, cwd=config.work)
    if result.returncode or len(result.stdout) > 4096:
        raise ValueError("Kanban CLI admission failed; inspect host logs and journal before retry")
    value = json.loads(result.stdout)
    if (type(value) is not dict or set(value) != {"kind", "board", "operation_id", "task_id", "status"}
            or value["kind"] != kind or value["board"] != kind or value["operation_id"] != payload["operation_id"]
            or not isinstance(value["task_id"], str) or not ENQUEUE.TASK_ID.fullmatch(value["task_id"])
            or value["status"] not in {"ready", "active", "review", "done"}):
        raise ValueError("invalid Kanban CLI result")
    return value


def admit(config, kind, key, payload):
    if kind not in KINDS or type(key) is not str or not hmac.compare_digest(key, config.keys[kind]):
        raise ValueError("unauthorized")
    operation = validate(payload, kind)
    grants = AUTHORITY.load(config.authority)
    repo = payload["repo"]
    if repo not in grants and f"{repo.split('/', 1)[0]}/*" not in grants:
        raise ValueError("repository is not granted")
    if not LOCK.acquire(timeout=5):
        raise ValueError("ingress busy")
    try:
        ENQUEUE.safe_dir(config.work)
        if kind == "pr-review":
            request = {"operation_id": operation["operation_id"], **payload}
            workspace = config.work / operation["operation_id"]
            if workspace.exists() or workspace.is_symlink():
                ENQUEUE.safe_dir(workspace)
                path = workspace / "request.json"
                if path.exists() or path.is_symlink():
                    raw = ENQUEUE.read_immutable(path)
                    previous = json.loads(raw)
                    if (type(previous) is not dict or set(previous) != set(request)
                            or raw != ENQUEUE.canonical(previous)
                            or validate({key: previous[key] for key in payload}, kind)["operation_id"] != operation["operation_id"]
                            or previous != {**request, "title": previous["title"]}):
                        raise ValueError("invalid review history")
                    request = previous
        else:
            request = round_request(config, payload)
        if request is None:
            return {"kind": kind, "board": kind, "operation_id": operation["operation_id"],
                    "task_id": None, "status": "capped"}
        return invoke(config, kind, request)
    finally:
        LOCK.release()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        kind = self.path.removeprefix("/v1/pr-tasks/")
        if self.path != f"/v1/pr-tasks/{kind}" or kind not in KINDS:
            return self.reply(404, {"error": "not found"})
        if self.headers.get("Host") not in {"127.0.0.1:8767", "host.docker.internal:8767"} or self.headers.get("Origin"):
            return self.reply(403, {"error": "invalid origin"})
        key = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(key, STATE.keys[kind]):
            return self.reply(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", ""))
            if length < 1 or length > 8192 or self.headers.get("Content-Type") != "application/json":
                raise ValueError("invalid request")
            body = self.rfile.read(length)
            def unique(pairs):
                value = {}
                for field, item in pairs:
                    if field in value:
                        raise ValueError("duplicate field")
                    value[field] = item
                return value
            payload = json.loads(body, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
            result = admit(STATE, kind, key, payload)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            return self.reply(503 if str(error) == "ingress busy" else 400, {"error": str(error)})
        except (OSError, subprocess.TimeoutExpired) as error:
            return self.reply(503, {"error": "Kanban CLI unavailable"})
        return self.reply(200, result)

    def reply(self, status, value):
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def read_key(path):
    path = Path(path)
    info, parent = path.lstat(), path.parent.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 65
            or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) & 0o077):
        raise ValueError("bridge key must be owner-only")
    value = path.read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid bridge key")
    return value


def main():
    parser = argparse.ArgumentParser()
    for name in ("review-key", "maintain-key", "authority", "work", "hermes-home", "hermes-bin"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    global STATE
    STATE = Config(Path(args.work), Path(args.authority), Path(args.hermes_home), Path(args.hermes_bin),
                   {"pr-review": read_key(args.review_key), "pr-maintain": read_key(args.maintain_key)})
    ENQUEUE.safe_dir(STATE.work, True)
    if not STATE.home.is_dir() or not os.access(STATE.hermes_bin, os.X_OK) or not (HERE / "hermes-pr-kanban-enqueue.py").is_file():
        raise SystemExit("host Hermes CLI not configured")
    ThreadingHTTPServer(("127.0.0.1", 8767), Handler).serve_forever()


if __name__ == "__main__":
    main()
