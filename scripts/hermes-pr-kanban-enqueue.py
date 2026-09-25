#!/usr/bin/env python3
"""Admit one inert direct-PR card through the supported Hermes CLI."""
import argparse, fcntl, hashlib, json, os, pathlib, re, stat, subprocess, sys
from hermes_direct_pr_journal import identity

CONFIG = {"pr-review": ("pr-review", "PR Review", "pr-review-v1", 1800), "pr-maintain": ("pr-maintain", "PR Maintain", "pr-maintain-v1", 2400)}
MODEL = "claude-sonnet-4-6"
REVIEW_SUMMARY = "Automatic execution stopped; human review required."
TASK_KEYS = {"id","title","body","assignee","status","priority","tenant","workspace_kind","workspace_path","branch_name","project_id","created_by","created_at","started_at","completed_at","result","skills","max_runtime_seconds","max_retries","model_override","provider_override","session_id","workflow_template_id","current_step_key","completion_contract","last_failure_error"}
BOARD_KEYS = {"slug","name","description","icon","color","default_workdir","project_id","created_at","archived","db_path","is_current","counts","total"}
SHOW_KEYS = {"task","latest_summary","parents","children","comments","events","runs"}
COMMENT_KEYS = {"author","body","created_at"}; EVENT_KEYS = {"kind","payload","created_at","run_id"}
RUN_KEYS = {"id","profile","step_key","status","outcome","summary","error","metadata","worker_pid","started_at","ended_at"}
TASK_ID = re.compile(r"t_[0-9a-f]{8}\Z"); MAX_ITEMS = 256; MAX_VALUE = 16384; LOCK_FD = None

def fail(message): raise ValueError(message)
def canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
def exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys: fail(f"{label} schema drift")
def verify_board(value, slug, name, home):
    exact(value, BOARD_KEYS, "board"); counts, total = value["counts"], value["total"]
    expected = {"slug":slug,"name":name,"description":"","icon":"","color":"","default_workdir":None,"project_id":None,"db_path":str(home/"kanban/boards"/slug/"kanban.db")}
    if any(value[key] != item for key,item in expected.items()) or value["archived"] is not False or type(value["is_current"]) is not bool or type(value["created_at"]) is not int or value["created_at"] <= 0 \
            or not isinstance(counts, dict) or any(not isinstance(key, str) or type(item) is not int or item < 0 for key,item in counts.items()) or type(total) is not int or total < 0 or total != sum(counts.values()): fail("board collision or drift")
def bounded(value, limit=MAX_VALUE):
    try: return len(canonical(value)) <= limit
    except (TypeError, ValueError): return False

def load_request(kind):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: fail(f"duplicate request key: {key}")
            value[key] = item
        return value
    try: value = json.loads(sys.stdin.read(), object_pairs_hook=unique, parse_constant=lambda _: fail("invalid JSON number"))
    except json.JSONDecodeError as error: raise ValueError("invalid request JSON") from error
    keys = {"operation_id","repo","number","url","title","head_sha"} | ({"feedback_digest","round"} if kind == "pr-maintain" else set())
    if not isinstance(value, dict) or set(value) != keys: fail("request keys differ from contract")
    strings = keys - {"number","round"}
    if any(not isinstance(value[key], str) or not value[key] for key in strings): fail("request strings must be nonempty")
    if type(value["number"]) is not int or value["number"] <= 0: fail("invalid PR number")
    if not 0 < len(value["title"]) <= 256: fail("invalid title")
    if value["url"] != f"https://github.com/{value['repo']}/pull/{value['number']}": fail("invalid GitHub PR URL")
    if kind == "pr-maintain" and (type(value["round"]) is not int or value["round"] not in range(1, 4)): fail("invalid maintenance round")
    try: operation = identity(kind, value["repo"], value["number"], value["head_sha"], value.get("feedback_digest"))
    except ValueError as error: raise ValueError("invalid request identity") from error
    if value["operation_id"] != operation["operation_id"]: fail("operation ID differs from canonical identity")
    return value, canonical(value)

def safe_dir(path, create=False):
    path = pathlib.Path(path)
    if not path.is_absolute(): fail("workspace root must be absolute")
    created = False
    if create:
        try: path.mkdir(mode=0o700); created = True
        except FileExistsError: pass
    if created:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fchmod(descriptor, 0o700)
        finally: os.close(descriptor)
    try: info = path.lstat()
    except OSError as error: raise ValueError("workspace directory unavailable") from error
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        fail("unsafe workspace directory")
    return path

def fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)

def read_immutable(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o440:
            fail("immutable file is unsafe")
        data = stream.read(MAX_VALUE + 1)
    if len(data) > MAX_VALUE: fail("immutable file is too large")
    return data

def immutable(path, data):
    try: existing = read_immutable(path)
    except FileNotFoundError: existing = None
    if existing is not None:
        if existing != data: fail("existing immutable file differs")
        return
    pattern = re.compile(rf"\.{re.escape(path.name)}\.tmp-[0-9]+-[0-9a-f]{{16}}\Z"); removed = False
    for temporary in path.parent.iterdir():
        if pattern.fullmatch(temporary.name): read_immutable(temporary); temporary.unlink(); removed = True
    if removed: fsync_dir(path.parent)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{os.urandom(8).hex()}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o440)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(descriptor, 0o440); stream.write(data); stream.flush(); os.fsync(descriptor)
        if path.exists() or path.is_symlink(): fail("immutable file appeared during publish")
        os.rename(temporary, path); fsync_dir(path.parent)
    except BaseException:
        try: read_immutable(temporary); temporary.unlink(); fsync_dir(path.parent)
        except FileNotFoundError: pass
        raise
    if read_immutable(path) != data: fail("immutable publish failed")

def binding(path):
    data = read_immutable(path)
    try: value = json.loads(data)
    except json.JSONDecodeError as error: raise ValueError("invalid task binding") from error
    exact(value, {"task_id"}, "task binding"); task_id = value["task_id"]
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id) or data != canonical(value): fail("invalid task binding")
    return task_id

def run(command, env, json_output=False, cwd=None):
    completed = subprocess.run(command, env=env, cwd=cwd, capture_output=True, text=True, timeout=30, pass_fds=() if LOCK_FD is None else (LOCK_FD,))
    if completed.returncode: fail(f"Hermes CLI failed: {completed.stderr.strip()[-300:]}")
    if not json_output: return completed.stdout.strip()
    try: return json.loads(completed.stdout)
    except json.JSONDecodeError as error: raise ValueError("Hermes CLI returned invalid JSON") from error

def show(command, env, cwd, board, task_id): return run([*command,"kanban","--board",board,"show",task_id,"--json"], env, True, cwd)
def closed(runs): return all(run["ended_at"] is not None and run["worker_pid"] is None and run["status"] not in {"claimed","running"} for run in runs)

def history(value):
    exact(value, SHOW_KEYS, "show")
    if value["parents"] != [] or value["children"] != []: fail("task graph drift")
    comments = value["comments"]
    if not isinstance(comments, list) or len(comments) > 100: fail("comment history drift")
    for comment in comments:
        exact(comment, COMMENT_KEYS, "comment"); author, body = comment["author"], comment["body"]
        if not isinstance(author, (str, type(None))) or not bounded(author) or not isinstance(body, str) or not body or len(body) > 16000 or type(comment["created_at"]) is not int: fail("comment history drift")
    if not isinstance(value["latest_summary"], (str, type(None))) or not bounded(value["latest_summary"]): fail("task summary drift")
    for items, keys, label in ((value["events"], EVENT_KEYS, "event"), (value["runs"], RUN_KEYS, "run")):
        if not isinstance(items, list) or len(items) > MAX_ITEMS: fail(f"{label} history drift")
        for item in items:
            exact(item, keys, label)
            if not bounded(item): fail(f"{label} history drift")
    for event in value["events"]:
        run_id = event["run_id"]
        if not isinstance(event["kind"], str) or not event["kind"] or len(event["kind"]) > 64 or type(event["created_at"]) is not int or not isinstance(event["payload"], (dict, type(None))) or (run_id is not None and (type(run_id) is not int or run_id <= 0)): fail("event history drift")
    for item in value["runs"]:
        strings = (item[key] for key in ("profile","step_key","status","outcome","summary","error"))
        if type(item["id"]) is not int or item["id"] <= 0 or not all(isinstance(field, (str, type(None))) for field in strings) or not isinstance(item["metadata"], (dict, type(None))) or any(field is not None and type(field) is not int for field in (item["worker_pid"],item["started_at"],item["ended_at"])): fail("run history drift")

def verify_task(task, task_id, body, title, workspace, tenant, runtime, status, assignees, operational=False):
    exact(task, TASK_KEYS, "task")
    expected = {"id":task_id,"body":body,"title":title,"priority":0,"model_override":MODEL,"provider_override":"anthropic","workspace_kind":"dir","workspace_path":str(workspace),"branch_name":None,"project_id":None,"tenant":tenant,
                "skills":[],"max_runtime_seconds":runtime,"max_retries":1,"created_by":"operator","session_id":None,"workflow_template_id":None,"current_step_key":None,"completion_contract":"local-only"}
    if any(task[key] != item for key,item in expected.items()) or task["status"] != status or task["assignee"] not in assignees or type(task["created_at"]) is not int or (task["started_at"] is not None and type(task["started_at"]) is not int): fail("Kanban card drift")
    terminal = status in {"done","archived"}
    if (terminal and type(task["completed_at"]) is not int) or (not terminal and task["completed_at"] is not None): fail("Kanban completion drift")
    if (not terminal and task["result"] is not None) or (terminal and not isinstance(task["result"], (str, type(None)))) or not bounded(task["result"]): fail("Kanban result drift")
    error = task["last_failure_error"]
    if error is not None and (not operational or not isinstance(error, str) or not error or len(error.encode()) > 4096): fail("Kanban failure drift")
    return task

def pristine(value, profile, workspace, tenant):
    payload = {"assignee":profile,"status":"blocked","parents":[],"creator_task_id":None,"tenant":tenant,"workspace_kind":"dir","workspace_path":str(workspace),"branch_name":None,"project_id":None,"skills":None,
               "goal_mode":None,"model_override":MODEL,"provider_override":"anthropic"}
    events = value["events"]
    return value["runs"] == [] and len(events) == 2 and events[0]["kind"] == "created" and events[0]["payload"] == payload and events[0]["run_id"] is None and events[1]["kind"] == "blocked" \
        and events[1]["payload"] == {"reason":"initial_status","status":"blocked","actor":"operator"} and events[1]["run_id"] is None

def enqueue(args):
    request, encoded = load_request(args.kind); operation = request["operation_id"]
    board, name, profile, runtime = CONFIG[args.kind]; root = safe_dir(args.workspace_root, True); workspace = safe_dir(root / operation, True)
    request_path = workspace / "request.json"; immutable(request_path, encoded); body = encoded.decode(); bind_path = workspace / "task-id.json"; intent_path = workspace / "create-intent.json"
    intent = canonical({"operation_id":operation,"request_digest":hashlib.sha256(encoded).hexdigest()}); bound = bind_path.exists() or bind_path.is_symlink(); intent_existed = intent_path.exists() or intent_path.is_symlink()
    if intent_existed: immutable(intent_path, intent)
    if bound != intent_existed: fail("create intent missing" if bound else "unresolved create outcome")
    env = {"HOME":str(pathlib.Path(args.hermes_home).parent),"HERMES_HOME":str(args.hermes_home),"PATH":os.environ.get("PATH", ""),"PYTHONUTF8":"1","PYTHONDONTWRITEBYTECODE":"1","HERMES_SAFE_MODE":"1"}; command = [str(args.hermes_bin)]
    boards_cmd = [*command,"kanban","boards","list","--all","--json"]; boards = run(boards_cmd, env, True, root)
    if not isinstance(boards, list): fail("invalid board listing")
    matches = [item for item in boards if isinstance(item, dict) and item.get("slug") == board]
    if not matches:
        run([*command,"kanban","boards","create",board,"--name",name], env, cwd=root); boards = run(boards_cmd, env, True, root)
        matches = [item for item in boards if isinstance(item, dict) and item.get("slug") == board]
    if len(matches) != 1: fail("board collision or drift")
    verify_board(matches[0], board, name, args.hermes_home)
    title = f"{request['repo']}#{request['number']} @ {request['head_sha'][:8]}"
    if bound: task_id = binding(bind_path); current = show(command, env, root, board, task_id)
    else:
        immutable(intent_path, intent); created = run([*command,"kanban","--board",board,"create",title,"--body-file",str(request_path),"--assignee",profile,"--workspace",f"dir:{workspace}","--idempotency-key",operation,"--tenant",operation,"--max-runtime",str(runtime),
            "--max-retries","1","--model",MODEL,"--provider","anthropic","--completion-contract","local-only","--created-by","operator","--initial-status","blocked","--json"], env, True, root)
        task_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id): fail("invalid task ID")
        current = show(command, env, root, board, task_id); history(current); initial = pristine(current, profile, workspace, operation)
        status = current["task"].get("status")
        if status not in {"blocked","ready","running","review","done"}: fail("invalid unbound card status")
        allowed = ({profile} if initial else {profile,None}) if status == "blocked" else ({profile,None} if status == "ready" else ({profile} if status == "running" else ({None} if status == "review" else {profile,None})))
        verify_task(created, task_id, body, title, workspace, operation, runtime, status, allowed, status == "blocked" and not initial)
        if created != current["task"]: fail("create/show task drift")
        immutable(bind_path, canonical({"task_id":task_id}))
    history(current); status = current["task"].get("status"); initial = pristine(current, profile, workspace, operation); manual_or_failed_block = status == "blocked" and not initial
    if status not in {"blocked","ready","running","review","done","archived"}: fail("invalid card status")
    allowed = (({profile} if initial else {profile,None}) if status == "blocked" else
               ({profile,None} if status == "ready" else ({profile} if status == "running" else ({None} if status == "review" else {profile,None}))))
    verify_task(current["task"], task_id, body, title, workspace, operation, runtime, status, allowed, manual_or_failed_block)
    if run([*command,"kanban","--board",board,"attachments",task_id,"--json"], env, True, root) != []: fail("card attachments drift")
    def refresh(expected, assignee, operational=False):
        value = show(command, env, root, board, task_id); history(value)
        verify_task(value["task"], task_id, body, title, workspace, operation, runtime, expected, {assignee}, operational); return value
    if status == "blocked" and initial:
        run([*command,"kanban","--board",board,"unblock",task_id], env, cwd=root); refresh("ready", profile); result = "ready"
    elif manual_or_failed_block:
        if not closed(current["runs"]): fail("card has an open run")
        if current["task"]["assignee"] is not None:
            run([*command,"kanban","--board",board,"assign",task_id,"none"], env, cwd=root); current = refresh("blocked", None, True)
        run([*command,"kanban","--board",board,"unblock",task_id], env, cwd=root); refresh("ready", None)
        run([*command,"kanban","--board",board,"request-review",task_id,"--summary",REVIEW_SUMMARY], env, cwd=root)
        reviewed = refresh("review", None)
        if reviewed["latest_summary"] != REVIEW_SUMMARY or not closed(reviewed["runs"]): fail("review state drift")
        result = "review"
    elif status == "ready" and current["task"]["assignee"] is None:
        if initial or not closed(current["runs"]): fail("invalid unassigned ready card")
        run([*command,"kanban","--board",board,"request-review",task_id,"--summary",REVIEW_SUMMARY], env, cwd=root)
        reviewed = refresh("review", None)
        if reviewed["latest_summary"] != REVIEW_SUMMARY or not closed(reviewed["runs"]): fail("review state drift")
        result = "review"
    elif status == "ready": result = "ready"
    elif status == "running": result = "active"
    else:
        if not closed(current["runs"]): fail("terminal card has an open run")
        result = "review" if status == "review" else "done"
    return {"status":result,"kind":args.kind,"board":board,"operation_id":operation,"task_id":task_id}

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--kind", choices=CONFIG, required=True); parser.add_argument("--hermes-home", type=pathlib.Path, required=True)
    parser.add_argument("--hermes-bin", type=pathlib.Path, required=True); parser.add_argument("--workspace-root", type=pathlib.Path, required=True)
    args = parser.parse_args(); global LOCK_FD
    try:
        descriptor = os.open(args.hermes_home / ".pr-kanban-enqueue.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); LOCK_FD = lock.fileno()
            print(json.dumps(enqueue(args), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError) as error: raise SystemExit(f"Hermes PR enqueue failed: {error}")

if __name__ == "__main__": main()
