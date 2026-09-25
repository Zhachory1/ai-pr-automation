#!/usr/bin/env python3
"""Isolated pinned-Hermes CLI contract preflight for direct PR cards."""
import argparse
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

MAX_OUTPUT = 1 << 20
TIMEOUT = 30
VERSION = "0.21.5"
SANDBOX = Path("/usr/bin/sandbox-exec")
POLICY = "(version 1)(allow default)(deny network*)"
REVIEW_SUMMARY = "Bounded isolated preflight review."
RESULT = "Isolated preflight complete."
TASK_KEYS = {"id","title","body","assignee","status","priority","tenant","workspace_kind",
             "workspace_path","branch_name","project_id","created_by","created_at","started_at",
             "completed_at","result","skills","max_runtime_seconds","max_retries","model_override",
             "provider_override","session_id","workflow_template_id","current_step_key",
             "completion_contract","last_failure_error"}
BOARD_KEYS = {"slug","name","description","icon","color","default_workdir","project_id","created_at",
              "archived","db_path","is_current","counts","total"}
SHOW_KEYS = {"task","latest_summary","parents","children","comments","events","runs"}
EVENT_KEYS = {"kind","payload","created_at","run_id"}
RUN_KEYS = {"id","profile","step_key","status","outcome","summary","error","metadata","worker_pid",
            "started_at","ended_at"}
DISPATCH_KEYS = {"reclaimed","crashed","timed_out","stale","auto_blocked","promoted",
                 "reaped_terminal_workers","spawned","skipped_unassigned","skipped_nonspawnable",
                 "skipped_per_profile_capped","auto_assigned_default","respawn_guarded","rate_limited",
                 "skipped_locked","memory_pressure"}


def fail(message):
    raise ValueError(message)


def exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        fail(f"{label} schema drift")


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value: fail(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def terminate_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        process.wait()


def execute(binary, arguments, environment, cwd, expect_json=False, with_raw=False):
    process = subprocess.Popen([str(SANDBOX), "-p", POLICY, str(binary), *arguments],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, cwd=cwd, start_new_session=True)
    selector = selectors.DefaultSelector(); buffers = {process.stdout:bytearray(), process.stderr:bytearray()}
    try:
        for stream in buffers: selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + TIMEOUT
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0: fail("CLI command timed out")
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    buffers[key.fileobj].extend(chunk)
                    if sum(map(len, buffers.values())) > MAX_OUTPUT: fail("CLI output exceeded limit")
                else:
                    selector.unregister(key.fileobj); key.fileobj.close()
        returncode = process.wait(timeout=max(0, deadline-time.monotonic()))
        output, error = bytes(buffers[process.stdout]), bytes(buffers[process.stderr])
        try: output_text, error_text = output.decode(), error.decode()
        except UnicodeDecodeError: fail("CLI output was not UTF-8")
        if returncode: fail(f"CLI command failed ({returncode}): {error_text.strip()[:300]}")
        if not expect_json: return output_text
        try: value = json.loads(output, object_pairs_hook=unique_object)
        except json.JSONDecodeError: fail("CLI returned malformed or multiple JSON values")
        return (output, value) if with_raw else value
    except BaseException:
        terminate_group(process)
        raise
    finally:
        selector.close()


def validate_task(task, task_id, workspace, status, assignee, result=None):
    exact(task, TASK_KEYS, "task")
    expected = {"id":task_id,"title":"direct-pr-preflight","body":"isolated fixture",
                "assignee":assignee,"status":status,"priority":0,"tenant":"preflight-tenant",
                "workspace_kind":"dir","workspace_path":str(workspace),"branch_name":None,
                "project_id":None,"created_by":"operator","started_at":None,"skills":[],
                "max_runtime_seconds":60,"max_retries":1,"model_override":None,
                "provider_override":None,"session_id":None,"workflow_template_id":None,
                "current_step_key":None,"completion_contract":"local-only","last_failure_error":None,
                "result":result}
    if any(task.get(key) != value for key, value in expected.items()) or type(task["created_at"]) is not int:
        fail("task identity or field drift")
    if status == "done":
        if type(task["completed_at"]) is not int: fail("task completion drift")
    elif task["completed_at"] is not None: fail("task completed early")


def validate_show(value, task_id, workspace, status, assignee, event_kinds, result=None):
    exact(value, SHOW_KEYS, "show"); validate_task(value["task"], task_id, workspace, status, assignee, result)
    if value["parents"] != [] or value["children"] != [] or value["comments"] != []: fail("task graph drift")
    if not isinstance(value["events"], list) or [event.get("kind") for event in value["events"]] != event_kinds:
        fail("task event drift")
    payloads = {"created":{"assignee":"fixture-profile","status":"blocked","parents":[],
                "creator_task_id":None,"tenant":"preflight-tenant","workspace_kind":"dir",
                "workspace_path":str(workspace),"branch_name":None,"project_id":None,"skills":None,
                "goal_mode":None,"model_override":None,"provider_override":None},
                "blocked":{"reason":"initial_status","status":"blocked","actor":"operator"},
                "assigned":{"assignee":None,"from":"fixture-profile"},"unblocked":None,
                "review_requested":{"summary":REVIEW_SUMMARY,"implementer":None,"reviewer":None},
                "completed":{"result_len":len(RESULT),"summary":RESULT}}
    for event in value["events"]:
        exact(event, EVENT_KEYS, "event")
        if event["payload"] != payloads[event["kind"]] or type(event["created_at"]) is not int:
            fail("event identity drift")
    if not isinstance(value["runs"], list): fail("runs schema drift")
    for run in value["runs"]:
        exact(run, RUN_KEYS, "run")
        if run["worker_pid"] is not None or run["status"] in {"claimed","running"} \
                or run["profile"] is not None or run["step_key"] is not None: fail("active worker drift")
    encoded = json.dumps(value)
    if "claim_lock" in encoded or "current_run_id" in encoded: fail("active claim drift")
    return value


def load_config(path):
    try: config = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as error: fail(f"invalid deployed Hermes config: {error}")
    if not isinstance(config, dict): fail("deployed Hermes config must be a mapping")
    kanban = config.get("kanban", {})
    if kanban is None: kanban = {}
    if not isinstance(kanban, dict): fail("deployed Hermes kanban config must be a mapping")
    if kanban.get("default_assignee") not in (None, ""): fail("kanban.default_assignee must be empty")


def preflight(binary, config_path, managed_dir):
    load_config(config_path)
    managed_dir = Path(managed_dir)
    if managed_dir.is_symlink() or (managed_dir.exists() and not managed_dir.is_dir()):
        fail("managed Hermes path must be a non-symlink directory or absent")
    managed_config = managed_dir/"config.yaml"
    if managed_config.exists() or managed_config.is_symlink(): load_config(managed_config)
    if not SANDBOX.is_file() or not os.access(SANDBOX, os.X_OK): fail("sandbox-exec unavailable")
    commands, writes, dispatch_spawns = [], 0, 0
    with tempfile.TemporaryDirectory(prefix="hermes-direct-pr-preflight-") as temporary:
        root = Path(temporary); home = root/"home"; hermes_home = root/"hermes"; workspace = root/"workspace"
        managed = root/"managed"; tmp = root/"tmp"
        for path in (root, home, hermes_home, workspace, managed, tmp):
            path.mkdir(mode=0o700, exist_ok=True); path.chmod(0o700)
        config = "updates:\n  check: false\nkanban:\n  default_assignee: null\n"
        (hermes_home/"config.yaml").write_text(config)
        profiles = hermes_home/"profiles"
        for name in ("default", "fixture-profile"):
            profile = profiles/name; profile.mkdir(parents=True); (profile/"config.yaml").write_text(config)
        (hermes_home/"active_profile").write_text("default\n"); marker = root/"worker-spawned"; sentinel = root/"worker-sentinel"
        sentinel.write_text(f"#!{sys.executable}\nimport os\nfd=os.open({str(marker)!r},os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)\nos.write(fd,b'spawned\\n')\nos.close(fd)\n"); sentinel.chmod(0o700)
        environment = {"HOME":str(home),"HERMES_HOME":str(hermes_home),"HERMES_MANAGED_DIR":str(managed),
                       "HERMES_BIN":str(sentinel),"TMPDIR":str(tmp),"PATH":"/usr/bin:/bin","PYTHONUTF8":"1",
                       "PYTHONDONTWRITEBYTECODE":"1","HERMES_SAFE_MODE":"1"}
        def run(label, args, as_json=False, write=False, raw=False):
            nonlocal writes
            commands.append(label); writes += int(write)
            return execute(binary, args, environment, root, as_json, raw)
        version_output = run("version", ["--version"])
        match = re.search(r"^Hermes Agent v(\S+)", version_output, re.MULTILINE)
        if not match or match.group(1) != VERSION: fail("pinned Hermes version drift")
        token = os.urandom(8).hex(); board = f"direct-pr-preflight-{token}"; name = f"Direct PR Preflight {token}"
        run("boards.create", ["kanban","boards","create",board,"--name",name], write=True)
        boards = run("boards.list", ["kanban","boards","list","--all","--json"], True)
        if not isinstance(boards, list) or len(boards) != 2: fail("board cardinality drift")
        for item in boards: exact(item, BOARD_KEYS, "board")
        matches = [item for item in boards if item["slug"] == board and item["name"] == name]
        target = matches[0] if len(matches) == 1 else fail("board identity drift")
        expected = {"description":"","icon":"","color":"","default_workdir":None,"project_id":None,
                    "archived":False,"db_path":str(hermes_home/"kanban/boards"/board/"kanban.db"),
                    "is_current":False,"counts":{},"total":0}
        if any(target[key] != value for key, value in expected.items()) or type(target["created_at"]) is not int:
            fail("board field drift")
        created = run("create", ["kanban","--board",board,"create","direct-pr-preflight","--body","isolated fixture",
            "--assignee","fixture-profile","--tenant","preflight-tenant","--idempotency-key",f"preflight-{token}",
            "--workspace",f"dir:{workspace}","--completion-contract","local-only","--max-runtime","60",
            "--max-retries","1","--created-by","operator","--initial-status","blocked","--json"], True, True)
        task_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(task_id, str) or not re.fullmatch(r"t_[0-9a-f]{8}", task_id): fail("task id drift")
        validate_task(created, task_id, workspace, "blocked", "fixture-profile"); events = ["created","blocked"]
        validate_show(run("show", ["kanban","--board",board,"show",task_id,"--json"], True), task_id, workspace, "blocked", "fixture-profile", events)
        run("assign", ["kanban","--board",board,"assign",task_id,"none"], write=True); events += ["assigned"]
        validate_show(run("show", ["kanban","--board",board,"show",task_id,"--json"], True), task_id, workspace, "blocked", None, events)
        run("unblock", ["kanban","--board",board,"unblock",task_id], write=True); events += ["unblocked"]
        expected_dispatch = {key:[] for key in DISPATCH_KEYS}; expected_dispatch.update({"reclaimed":0,"promoted":0,"skipped_unassigned":[task_id],"skipped_locked":False,"memory_pressure":None})
        def prove_dispatch(status):
            nonlocal dispatch_spawns
            before_raw, before = run("show", ["kanban","--board",board,"show",task_id,"--json"], True, raw=True)
            validate_show(before, task_id, workspace, status, None, events)
            dispatch = run("dispatch", ["kanban","--board",board,"dispatch","--max","1","--json"], True)
            after_raw, after = run("show", ["kanban","--board",board,"show",task_id,"--json"], True, raw=True)
            marker_seen = marker.exists(); spawned = dispatch.get("spawned") if isinstance(dispatch, dict) else None
            dispatch_spawns += (len(spawned) if isinstance(spawned, list) else 0) + int(marker_seen)
            if dispatch != expected_dispatch or spawned != [] or dispatch.get("auto_assigned_default") != []: fail("live dispatch output drift")
            if marker_seen: fail("live dispatch spawned worker sentinel")
            if after_raw != before_raw or after != before: fail("live dispatch mutated task state")
            return validate_show(after, task_id, workspace, status, None, events)
        prove_dispatch("ready")
        run("request-review", ["kanban","--board",board,"request-review",task_id,"--summary",REVIEW_SUMMARY], write=True); events += ["review_requested"]
        review = prove_dispatch("review")
        if review["latest_summary"] != REVIEW_SUMMARY: fail("review summary drift")
        run("complete", ["kanban","--board",board,"complete",task_id,"--result",RESULT], write=True); events += ["completed"]
        done = validate_show(run("show", ["kanban","--board",board,"show",task_id,"--json"], True), task_id, workspace, "done", None, events, RESULT)
        if done["latest_summary"] != RESULT: fail("completion summary drift")
    return {"ready":True,"version":VERSION,"commands":commands,"writes":writes,
            "dispatch_spawns":dispatch_spawns,"network_policy":"sandbox-deny"}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--hermes-bin", required=True); parser.add_argument("--config", required=True); parser.add_argument("--managed-dir", required=True); args = parser.parse_args()
    try: report = preflight(args.hermes_bin, args.config, args.managed_dir)
    except (OSError, ValueError) as error: raise SystemExit(f"Hermes direct PR Kanban preflight failed: {error}")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__": main()
