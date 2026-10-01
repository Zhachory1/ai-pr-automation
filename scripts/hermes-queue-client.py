#!/usr/bin/env python3
"""Typed local intake for the host Hermes Kanban CLI (run as hermes-agent)."""
import argparse
import hashlib
import json
import os
import pathlib
import pwd
import re
import subprocess
import sys

DOCUMENTS = {"prd-write": "prd", "design-write": "design", "roadmap-write": "roadmap"}
OPERATION = re.compile(r"(prd|design|roadmap)-[0-9a-f]{64}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
ROOT = pathlib.Path(__file__).resolve().parent


def fail(message):
    raise ValueError(message)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: fail("duplicate JSON key")
        result[key] = value
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def intake(raw):
    if len(raw) > 8192: fail("request exceeds 8 KiB")
    try: value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as error: raise ValueError("invalid request JSON") from error
    if not isinstance(value, dict) or set(value) != {"version", "kind", "title", "requirements", "repositories"} or \
            type(value["version"]) is not int or value["version"] != 1 or \
            not isinstance(value["kind"], str) or value["kind"] not in DOCUMENTS:
        fail("unsupported request contract")
    if not isinstance(value["title"], str) or not 0 < len(value["title"]) <= 256 or \
            not isinstance(value["requirements"], str) or not 0 < len(value["requirements"].encode()) <= 2048:
        fail("invalid title or requirements")
    repos = value["repositories"]
    if not isinstance(repos, list) or not 0 < len(repos) <= 5 or any(
        not isinstance(repo, str) or not REPO.fullmatch(repo) or any(part in {".", ".."} for part in repo.split("/"))
        for repo in repos) or len(repos) != len(set(repos)):
        fail("invalid repositories")
    return value


def host():
    account = pwd.getpwnam("hermes-agent")
    if os.geteuid() != account.pw_uid: fail("run as the host hermes-agent account; do not use personal Hermes")
    home = pathlib.Path(account.pw_dir)
    hermes_home = home / ".hermes"
    binary = home / ".local/bin/hermes"
    if not hermes_home.is_dir() or not binary.is_file(): fail("host Hermes installation unavailable")
    env = {"HOME": str(home), "HERMES_HOME": str(hermes_home), "PATH": os.environ.get("PATH", ""),
           "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "HERMES_SAFE_MODE": "1"}
    return home, hermes_home, binary, env


def run(command, env, input_data=None):
    try: result = subprocess.run(command, input=input_data, capture_output=True, env=env, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as error: raise ValueError("Hermes command unavailable or timed out") from error
    if result.returncode: fail("Hermes command failed; operator must inspect host logs")
    try: return json.loads(result.stdout, object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as error: raise ValueError("invalid Hermes JSON response") from error


def status(operation, binary, env):
    if not OPERATION.fullmatch(operation): fail("invalid operation ID")
    board = next(board for board, prefix in DOCUMENTS.items() if operation.startswith(prefix + "-"))
    tasks = run([str(binary), "kanban", "--board", board, "list", "--tenant", operation, "--archived", "--json"], env)
    if not isinstance(tasks, list) or len(tasks) > 100: fail("invalid Kanban task listing")
    writers = []
    for task in tasks:
        if not isinstance(task, dict) or task.get("tenant") != operation: fail("Kanban operation mismatch")
        try: body = json.loads(task["body"], object_pairs_hook=unique)
        except (KeyError, ValueError, TypeError) as error: raise ValueError("invalid Kanban task body") from error
        if isinstance(body, dict) and body.get("workflow") == board and body.get("operation") == operation and \
                body.get("stage") == "writer" and type(body.get("round")) is int and body["round"] == 0:
            writers.append(task)
    if len(writers) != 1 or not isinstance(writers[0].get("id"), str) or not isinstance(writers[0].get("status"), str):
        fail("operation missing or ambiguous")
    writer = writers[0]
    return {"version": 1, "kind": board, "operation_id": operation, "board": board,
            "task_id": writer["id"], "writer_status": writer["status"]}


def request(value, hermes_home, binary, env):
    authority = pathlib.Path("/usr/local/etc/ai-pr-automation/authority.yaml")
    for repo in value["repositories"]:
        check = subprocess.run([sys.executable, str(ROOT / "hermes-authority.py"), "--file", str(authority), "--check", repo],
                               capture_output=True, env=env, timeout=5)
        if check.returncode: fail(f"repository not granted: {repo}")
    kind = value["kind"]
    if kind == "roadmap-write":
        boards = run([str(binary), "kanban", "boards", "list", "--all", "--json"], env)
        if not isinstance(boards, list) or not any(isinstance(board, dict) and board.get("slug") == kind and
                                                   board.get("name") == "Roadmap Write" and board.get("archived") is False for board in boards):
            fail("roadmap board not activated")
    core = {"title": value["title"], "requester": "local-agent", "requirements": value["requirements"],
            "repositories": value["repositories"]}
    operation = DOCUMENTS[kind] + "-" + hashlib.sha256(canonical(core)).hexdigest()
    payload = canonical({"operation_id": operation, **core})
    result = run([sys.executable, str(ROOT / "hermes-prd-kanban-enqueue.py"), "--engine", "dynamic",
                  "--document-kind", DOCUMENTS[kind], "--hermes-home", str(hermes_home),
                  "--hermes-bin", str(binary)], env, payload)
    if not isinstance(result, dict) or result.get("board") != kind or result.get("operation_id") != operation or \
            not isinstance(result.get("tasks"), dict) or not isinstance(result["tasks"].get("writer"), str):
        fail("ambiguous Hermes admission response")
    current = status(operation, binary, env)
    if current["task_id"] != result["tasks"]["writer"]: fail("Kanban writer binding differs")
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("request")
    show = actions.add_parser("status")
    show.add_argument("operation_id")
    args = parser.parse_args()
    try:
        _, hermes_home, binary, env = host()
        output = request(intake(sys.stdin.buffer.read(8193)), hermes_home, binary, env) if args.action == "request" \
            else status(args.operation_id, binary, env)
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"Hermes queue client: {error}")


if __name__ == "__main__": main()
