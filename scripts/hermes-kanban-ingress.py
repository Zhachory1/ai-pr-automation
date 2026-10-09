#!/usr/bin/env python3
"""Authenticated loopback ingress for Compose PR discovery into the host's Kanban CLI."""
import argparse
from collections import namedtuple
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import threading
import time
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
COUNCIL = load_module("hermes_pr_review_council", HERE / "hermes-pr-review-council.py")
PUBLISHER = load_module("hermes_pr_review_council_publish", HERE / "hermes-pr-review-council-publish.py")
GITHUB = load_module("hermes_pr_review_github", HERE / "hermes_pr_review_github.py")
Config = namedtuple("Config", "work authority home hermes_bin keys history history_dirs history_error council_install council_enabled",
                    defaults=(None, None, None, None, False))
LOCK = threading.Lock()
STATE = None
KINDS = {"pr-review", "pr-maintain"}
DEFERRED = object()


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


def history_entry(entry):
    ENQUEUE.safe_dir(entry)
    path = entry / "request.json"
    if not path.exists() and not path.is_symlink():
        return None
    raw = ENQUEUE.read_immutable(path)
    previous = json.loads(raw)
    if (raw != ENQUEUE.canonical(previous) or type(previous) is not dict
            or set(previous) != {"operation_id", "repo", "number", "url", "title", "head_sha", "feedback_digest", "round"}
            or type(previous["round"]) is not int or previous["round"] not in range(1, 4)
            or validate({key: previous[key] for key in ("repo", "number", "url", "title", "head_sha", "feedback_digest")}, "pr-maintain")["operation_id"] != previous["operation_id"]
            or entry.name != previous["operation_id"]):
        raise ValueError("invalid maintenance history")
    return previous


def add_history(history, entry):
    previous = history_entry(entry)
    if previous is None:
        return
    seen = history.setdefault((previous["repo"].lower(), previous["number"]), {})
    digest = previous["feedback_digest"]
    if digest in seen and seen[digest][0]["operation_id"] != previous["operation_id"]:
        raise ValueError("conflicting maintenance history")
    seen[digest] = (previous, entry)
    if len(seen) > 3:
        raise ValueError("maintenance round cap exceeded")


def load_history(work, known=None):
    history = {}
    for entry in work.iterdir():
        if re.fullmatch(r"pr-maintain-[0-9a-f]{64}", entry.name):
            if known is not None:
                known.add(entry.name)
            add_history(history, entry)
    return history


def prepare_history(config):
    known = set()
    try:
        history = load_history(config.work, known)
    except (OSError, ValueError):
        print("maintenance history invalid; maintenance admissions disabled until reconciliation", file=sys.stderr)
        return config._replace(history={}, history_dirs=set(), history_error=[True])
    return config._replace(history=history, history_dirs=known, history_error=[])


def prior_binding(previous, entry):
    intent = entry / "create-intent.json"
    if not intent.exists() and not intent.is_symlink():
        raise ValueError("prior maintenance request incomplete for " + previous["operation_id"])
    expected = ENQUEUE.canonical({"operation_id": previous["operation_id"],
                                  "request_digest": hashlib.sha256(ENQUEUE.canonical(previous)).hexdigest()})
    if ENQUEUE.read_immutable(intent) != expected:
        raise ValueError("invalid prior maintenance intent")
    binding = entry / "task-id.json"
    if not binding.exists() and not binding.is_symlink():
        raise ValueError("unresolved create outcome for " + previous["operation_id"])
    return ENQUEUE.binding(binding)


def round_request(config, payload):
    repo, number, digest = payload["repo"].lower(), payload["number"], payload["feedback_digest"]
    deadline = time.monotonic() + 3
    if config.history_dirs is not None:
        current = set()
        for entry in config.work.iterdir():
            if time.monotonic() >= deadline:
                raise TimeoutError("maintenance inventory timed out")
            if re.fullmatch(r"pr-maintain-[0-9a-f]{64}", entry.name):
                current.add(entry.name)
        if current != config.history_dirs:
            raise ValueError("maintenance history changed outside ingress")
    history = config.history if config.history is not None else load_history(config.work)
    seen = history.get((repo, number), {})
    if config.history is not None:
        for previous, entry in seen.values():
            if history_entry(entry) != previous:
                raise ValueError("maintenance history changed outside ingress")
    if time.monotonic() >= deadline:
        raise TimeoutError("maintenance inventory timed out")
    if digest in seen:
        previous, entry = seen[digest]
        prior_binding(previous, entry)
        return previous
    if len(seen) == 3:
        return None
    pending = [(previous, entry, prior_binding(previous, entry)) for previous, entry in seen.values()]
    env = {"HOME": str(config.home.parent), "HERMES_HOME": str(config.home), "PATH": os.environ.get("PATH", ""),
           "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "HERMES_SAFE_MODE": "1"}
    for previous, entry, task_id in pending:
        remaining = min(2, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("prior maintenance status timed out")
        shown = ENQUEUE.show([str(config.hermes_bin)], env, config.work, "pr-maintain", task_id, timeout=remaining)
        ENQUEUE.history(shown)
        task = shown["task"]
        status = task.get("status") if isinstance(task, dict) else None
        if status not in {"blocked", "ready", "running", "review", "done", "archived"}:
            raise ValueError("invalid prior maintenance task")
        _, _, profile, runtime = ENQUEUE.CONFIG["pr-maintain"]
        ENQUEUE.verify_task(task, task_id, ENQUEUE.canonical(previous).decode(),
                            f"{previous['repo']}#{previous['number']} @ {previous['head_sha'][:8]}",
                            entry, previous["operation_id"], runtime, status, {profile, None}, True)
        if time.monotonic() >= deadline:
            raise TimeoutError("prior maintenance status timed out")
        if status not in {"done", "archived"}:
            return DEFERRED
        if not ENQUEUE.closed(shown["runs"]):
            raise ValueError("prior maintenance card has open run")
    if time.monotonic() >= deadline:
        raise TimeoutError("maintenance inventory timed out")
    operation = validate(payload, "pr-maintain")
    return {"operation_id": operation["operation_id"], **payload, "round": len(seen) + 1}


def invoke(config, kind, payload):
    command = [sys.executable, "-B", str(HERE / "hermes-pr-kanban-enqueue.py"), "--kind", kind,
               "--hermes-home", str(config.home), "--hermes-bin", str(config.hermes_bin),
               "--workspace-root", str(config.work)]
    result = subprocess.run(command, input=ENQUEUE.canonical(payload), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=120, cwd=config.work)
    if result.returncode and result.stderr.strip() == b"Hermes PR enqueue failed: unresolved create outcome":
        raise ValueError("unresolved create outcome")
    if result.returncode or len(result.stdout) > 4096:
        raise ValueError("Kanban CLI admission failed; inspect host logs and journal before retry")
    value = json.loads(result.stdout)
    if (type(value) is not dict or set(value) != {"kind", "board", "operation_id", "task_id", "status"}
            or value["kind"] != kind or value["board"] != kind or value["operation_id"] != payload["operation_id"]
            or not isinstance(value["task_id"], str) or not ENQUEUE.TASK_ID.fullmatch(value["task_id"])
            or value["status"] not in {"ready", "active", "review", "done"}):
        raise ValueError("invalid Kanban CLI result")
    return value


def validate_council_runtime(config):
    install = config.council_install
    helper = config.home / "bin/hermes-council-tools"
    python = install / "venv/bin/python" if install is not None else None
    if (not install or not install.is_absolute() or install.is_symlink()
            or not (install / "hermes_cli/kanban_db.py").is_file()
            or not helper.is_file() or helper.is_symlink()
            or helper.read_bytes() != (HERE.parent / "bin/hermes-council-tools").read_bytes()
            or not python.is_file() or not os.access(python, os.X_OK)):
        raise ValueError("pinned council runtime or restricted tool is unavailable")
    expected = {"PR_REVIEW_COUNCIL_REPO_ROOT": str(config.home.parent / "code"),
                "PR_REVIEW_COUNCIL_WORKFLOW_ROOT": str(config.work),
                "HERMES_COUNCIL_TOOLS_BIN": str(helper),
                "HERMES_COUNCIL_TOOLS_PYTHON": str(python)}
    for profile in [*COUNCIL.PROFILES.values(), COUNCIL.SECURITY, "pr-review-synthesis-v2"]:
        source = HERE.parent / "agent-config/hermes/profiles" / profile
        target = config.home / "profiles" / profile
        if not target.is_dir() or target.is_symlink():
            raise ValueError("restricted review profile is not installed")
        for name in ("config.yaml", "SOUL.md"):
            path = target / name
            if path.is_symlink() or not path.is_file() or path.read_bytes() != (source / name).read_bytes():
                raise ValueError("installed review profile differs from source")
        env = target / ".env"
        if env.is_symlink() or not env.is_file() or env.stat().st_mode & 0o077:
            raise ValueError("review profile environment is not owner-only")
        values = {}
        for line in env.read_text().splitlines():
            key, separator, raw = line.partition("=")
            if not separator or key in values:
                raise ValueError("review profile environment differs")
            values[key] = json.loads(raw)
        if any(values.get(key) != value for key, value in expected.items()):
            raise ValueError("review profile snapshot/tool roots differ")


def council_capacity(config, operation):
    active = 0
    for entry in config.work.iterdir():
        if not re.fullmatch(r"pr-review-[0-9a-f]{64}", entry.name):
            continue
        ENQUEUE.safe_dir(entry)
        owner_path = entry / "request.json"
        if not owner_path.exists() and not owner_path.is_symlink():
            continue
        owner = json.loads(ENQUEUE.read_immutable(owner_path))
        if not isinstance(owner, dict) or owner.get("route") != "council-v2":
            continue
        if entry.name == operation:
            return
        receipt_path = entry / "review-outcome.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            outcome = json.loads(COUNCIL._immutable_file(receipt_path))
            if (not isinstance(outcome, dict) or outcome.get("operation_id") != entry.name
                    or not (outcome.get("status") == "superseded" and outcome.get("review_id") is None
                            or type(outcome.get("review_id")) is int and outcome["review_id"] > 0)):
                raise ValueError("council settlement receipt differs")
            continue
        active += 1
    if active >= 2:
        raise ValueError("review council active-head capacity reached")


def queue_review(config, request):
    council_capacity(config, request["operation_id"])
    route = COUNCIL.queue(ENQUEUE, config.work, request)
    if route == "legacy":
        return invoke(config, "pr-review", request)
    receipt = config.work / request["operation_id"] / "review-outcome.json"
    if receipt.exists() or receipt.is_symlink():
        value = json.loads(COUNCIL._immutable_file(receipt))
        if value.get("operation_id") != request["operation_id"] or value.get("head_sha") != request["head_sha"]:
            raise ValueError("review outcome does not match admission")
        if value.get("status") == "superseded" and value.get("task_id") is None and value.get("review_id") is None:
            if any((receipt.parent / name).exists() or (receipt.parent / name).is_symlink()
                   for name in ("snapshot/diff.patch", "input/context.json", "input/identity.json")):
                raise ValueError("superseded council cleanup incomplete")
            return {"kind": "pr-review", "board": COUNCIL.BOARD, "operation_id": request["operation_id"],
                    "task_id": None, "status": "superseded"}
        cleanup = receipt.parent / "review-cleanup.json"
        if cleanup.exists() or cleanup.is_symlink():
            proof = json.loads(COUNCIL._immutable_file(cleanup))
            if proof.get("operation_id") != request["operation_id"] or proof.get("review_id") != value["review_id"]:
                raise ValueError("review evidence cleanup differs")
            return {"kind": "pr-review", "board": COUNCIL.BOARD, "operation_id": request["operation_id"],
                    "task_id": value["task_id"], "status": "done"}
    return {"kind": "pr-review", "board": COUNCIL.BOARD, "operation_id": request["operation_id"],
            "task_id": None, "status": "deferred"}


def invoke_council(config, request, repository_path=None):
    install = config.council_install
    if not install or not install.is_absolute() or not (install / "hermes_cli/kanban_db.py").is_file():
        raise ValueError("pinned council Kanban runtime unavailable")
    if str(install) not in sys.path:
        sys.path.insert(0, str(install))
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    council_capacity(config, request["operation_id"])
    operation = request["operation_id"]
    workspace = config.work / operation
    outcome_path = workspace / "review-outcome.json"
    if repository_path is None and not (outcome_path.exists() or outcome_path.is_symlink()):
        raise ValueError("local review checkout is required")
    if not kb.board_exists(COUNCIL.BOARD):
        kb.create_board(COUNCIL.BOARD, name="PR Review", description="")
    github = GITHUB.GitHub()
    manifest_path = workspace / "input/identity.json"
    if outcome_path.exists() or outcome_path.is_symlink():
        outcome = json.loads(COUNCIL._immutable_file(outcome_path))
        if outcome.get("status") == "verified":
            spec = outcome
            if manifest_path.exists() or manifest_path.is_symlink():
                manifest = json.loads(COUNCIL._immutable_file(manifest_path))
                spec = COUNCIL.plan({"operation_id": operation, "repo": request["repo"],
                                     "number": request["number"], "head_sha": request["head_sha"],
                                     "base_sha": manifest["base_sha"], "diff_digest": manifest["diff_digest"],
                                     "context_digest": manifest["context_digest"],
                                     "changed_paths": manifest["changed_paths"],
                                     **({"repository_path": manifest["repository_path"]}
                                        if "repository_path" in manifest else {})})
            PUBLISHER.purge_verified(workspace / "review-effects.sqlite", workspace, spec, outcome["task_id"])
    with kbc.connect_closing(board=COUNCIL.BOARD) as conn:
        admitted = COUNCIL.admit(kb, conn, ENQUEUE, github, config.work, request, repository_path)
        if admitted["route"] == "legacy":
            return invoke(config, "pr-review", request)
        result = {"kind": "pr-review", "board": COUNCIL.BOARD, "operation_id": operation,
                  "task_id": admitted["task_id"], "status": "active"}
        if admitted.get("status") == "done":
            outcome = json.loads(COUNCIL._immutable_file(outcome_path))
            PUBLISHER.redact_verified(conn, workspace / "review-effects.sqlite", workspace, outcome)
            return {**result, "status": "done"}
        manifest = json.loads(COUNCIL._immutable_file(workspace / "input/identity.json"))
        spec = COUNCIL.plan({"operation_id": operation, "repo": request["repo"],
                             "number": request["number"], "head_sha": request["head_sha"],
                             "base_sha": manifest["base_sha"], "diff_digest": manifest["diff_digest"],
                             "context_digest": manifest["context_digest"], "changed_paths": manifest["changed_paths"],
                             **({"repository_path": manifest["repository_path"]}
                                if "repository_path" in manifest else {})})
        tasks = {}
        for role in [*spec["specialists"], "synthesis"]:
            row = conn.execute("SELECT id FROM tasks WHERE idempotency_key=?",
                               (f'{spec["workflow_id"]}:{role}',)).fetchone()
            if row is None:
                raise ValueError("council graph binding missing")
            tasks[role] = row["id"]
        if tasks["synthesis"] != admitted["task_id"]:
            raise ValueError("council synthesis task differs")
        publication = {key: request[key] for key in ("operation_id", "repo", "number", "head_sha")}
        publication.update(base_sha=spec["base_sha"], artifact_digest=spec["artifact_digest"])
        settled = PUBLISHER.settle(kb, conn, tasks, spec, workspace / "review-effects.sqlite",
                                   github, publication, workspace)
        if settled:
            outcome = json.loads(COUNCIL._immutable_file(outcome_path))
            PUBLISHER.redact_verified(conn, workspace / "review-effects.sqlite", workspace, outcome)
            return {**result, "status": "done"}
        return result


def admit(config, kind, key, payload):
    if kind not in KINDS or type(key) is not str or not hmac.compare_digest(key, config.keys[kind]):
        raise ValueError("unauthorized")
    operation = validate(payload, kind)
    grants = AUTHORITY.load(config.authority)
    repo = payload["repo"]
    if repo not in grants and f"{repo.split('/', 1)[0]}/*" not in grants:
        raise ValueError("repository is not granted")
    if kind == "pr-maintain" and config.history_error:
        raise ValueError("maintenance history invalid; reconcile before retry")
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
                    if isinstance(previous, dict) and previous.get("route") == "council-v2":
                        if not config.council_enabled:
                            raise ValueError("council owner requires reconciliation before legacy admission")
                        return queue_review(config, request)
                    if (type(previous) is not dict or set(previous) != set(request)
                            or raw != ENQUEUE.canonical(previous)
                            or validate({key: previous[key] for key in payload}, kind)["operation_id"] != operation["operation_id"]
                            or previous != {**request, "title": previous["title"]}):
                        raise ValueError("invalid review history")
                    request = previous
                    return invoke(config, kind, request)
        else:
            request = round_request(config, payload)
        if request is None or request is DEFERRED:
            return {"kind": kind, "board": kind, "operation_id": operation["operation_id"],
                    "task_id": None, "status": "capped" if request is None else "deferred"}
        if kind == "pr-maintain":
            try:
                return invoke(config, kind, request)
            finally:
                if config.history is not None:
                    entry = config.work / request["operation_id"]
                    if entry.exists() or entry.is_symlink():
                        try:
                            add_history(config.history, entry)
                        except (OSError, ValueError):
                            if config.history_error is not None:
                                config.history_error.append(True)
                            raise
                        if config.history_dirs is not None:
                            config.history_dirs.add(entry.name)
        return queue_review(config, request) if kind == "pr-review" and config.council_enabled else invoke(config, kind, request)
    finally:
        LOCK.release()


class Handler(BaseHTTPRequestHandler):
    _preauth_seconds = 10

    def handle_one_request(self):
        self._preauth_deadline = time.monotonic() + self._preauth_seconds
        def expire():
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._preauth_timer = threading.Timer(self._preauth_seconds, expire)
        self._preauth_timer.daemon = True
        self._preauth_timer.start()
        try:
            self.connection.settimeout(self._preauth_seconds)
            super().handle_one_request()
        finally:
            self._preauth_timer.cancel()

    def do_POST(self):
        kind = self.path.removeprefix("/v1/pr-tasks/")
        if self.path != f"/v1/pr-tasks/{kind}" or kind not in KINDS:
            return self.reply(404, {"error": "not found"})
        if self.headers.get("Host") not in {"127.0.0.1:8767", "host.docker.internal:8767"} or self.headers.get("Origin"):
            return self.reply(403, {"error": "invalid origin"})
        timestamp = self.headers.get("X-Hermes-Timestamp", "")
        signature = self.headers.get("X-Hermes-Signature", "")
        if (not re.fullmatch(r"[0-9]{10,12}", timestamp) or abs(time.time() - int(timestamp)) > 60
                or not re.fullmatch(r"[0-9a-f]{64}", signature)):
            return self.reply(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", ""))
            if length < 1 or length > 8192 or self.headers.get("Content-Type") != "application/json":
                raise ValueError("invalid request")
            body = self.rfile.read(length)
            signed = b"POST\n" + self.path.encode() + b"\n" + timestamp.encode() + b"\n" + body
            expected = hmac.new(bytes.fromhex(STATE.keys[kind]), signed, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return self.reply(401, {"error": "unauthorized"})
            self._preauth_timer.cancel()
            if time.monotonic() >= self._preauth_deadline:
                return self.reply(408, {"error": "request timeout"})
            def unique(pairs):
                value = {}
                for field, item in pairs:
                    if field in value:
                        raise ValueError("duplicate field")
                    value[field] = item
                return value
            payload = json.loads(body, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
            result = admit(STATE, kind, STATE.keys[kind], payload)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            return self.reply(503 if str(error) == "ingress busy" else 400, {"error": str(error)})
        except TimeoutError:
            return self.reply(408, {"error": "request timeout"})
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


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def read_key(path):
    path = Path(path)
    info, parent = path.lstat(), path.parent.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size not in {64, 65}
            or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) & 0o077):
        raise ValueError("bridge key must be owner-only")
    value = path.read_bytes()
    if not re.fullmatch(rb"[0-9a-f]{64}\n?", value):
        raise ValueError("invalid bridge key")
    return value.decode().rstrip("\n")


def main():
    parser = argparse.ArgumentParser()
    for name in ("review-key", "maintain-key", "authority", "work", "hermes-home", "hermes-bin"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--review-council-install", help="pinned Hermes runtime source directory; enables council admission")
    args = parser.parse_args()
    global STATE
    STATE = Config(Path(args.work), Path(args.authority), Path(args.hermes_home), Path(args.hermes_bin),
                   {"pr-review": read_key(args.review_key), "pr-maintain": read_key(args.maintain_key)},
                   council_install=Path(args.review_council_install) if args.review_council_install else None,
                   council_enabled=bool(args.review_council_install))
    ENQUEUE.safe_dir(STATE.work, True)
    STATE = prepare_history(STATE)
    if not STATE.home.is_dir() or not os.access(STATE.hermes_bin, os.X_OK) or not (HERE / "hermes-pr-kanban-enqueue.py").is_file():
        raise SystemExit("host Hermes CLI not configured")
    if STATE.council_enabled:
        validate_council_runtime(STATE)
    BoundedHTTPServer(("127.0.0.1", 8767), Handler).serve_forever()


if __name__ == "__main__":
    main()
