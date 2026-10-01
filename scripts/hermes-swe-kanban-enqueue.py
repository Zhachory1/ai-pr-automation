#!/usr/bin/env python3
"""Admit one human-approved SWE task after pinning an isolated local workspace."""
import argparse, hashlib, json, os, pathlib, re, stat, subprocess, sys, time

REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
TASK = re.compile(r"^t_[0-9a-f]{8}$")
SOURCE_TYPES = {"approved-design", "approved-plan", "issue", "handoff", "prompt"}
BOARD, BOARD_NAME, PROFILE = "swe-implement", "SWE Implement", "swe-implement-v1"


def fail(message): raise ValueError(message)
def canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def load_request():
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: fail(f"duplicate request key: {key}")
            value[key] = item
        return value
    raw = getattr(sys.stdin, "buffer", sys.stdin).read(); raw = raw.encode() if isinstance(raw, str) else raw
    try: value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: fail("invalid JSON number"))
    except json.JSONDecodeError as error: raise ValueError("invalid request JSON") from error
    keys = {"operation_id","repository","base_branch","base_sha","title","problem","acceptance_criteria","non_goals","validation_expectations","source_type","source_refs","human_approval","remote_effects"}
    if not isinstance(value, dict) or set(value) != keys: fail("request keys differ from contract")
    if not REPO.fullmatch(value["repository"]) or any(part in {".",".."} for part in value["repository"].split("/")): fail("invalid repository")
    if not isinstance(value["base_branch"], str) or not value["base_branch"] or len(value["base_branch"]) > 200 or not SHA.fullmatch(value["base_sha"]): fail("invalid base identity")
    for key, limit in (("title",256),("problem",16384)):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > limit: fail(f"invalid {key}")
    for key in ("acceptance_criteria","non_goals","validation_expectations"):
        items = value[key]
        if not isinstance(items, list) or not items or len(items) > 100 or any(not isinstance(item,str) or not item.strip() or len(item)>4096 for item in items): fail(f"invalid {key}")
    if value["source_type"] not in SOURCE_TYPES: fail("invalid source type")
    refs = value["source_refs"]
    if not isinstance(refs,list) or not refs or len(refs)>50 or any(not isinstance(item,dict) or set(item) not in ({"ref"},{"ref","sha256"}) or not isinstance(item["ref"],str) or not item["ref"] or len(item["ref"])>2048 or ("sha256" in item and not re.fullmatch(r"[0-9a-f]{64}",str(item["sha256"]))) for item in refs): fail("invalid source refs")
    approval = value["human_approval"]
    if not isinstance(approval,dict) or set(approval)!={"approved_by","approved_at","scope"} or approval.get("scope")!="implementation-and-draft-pr" or any(not isinstance(approval.get(key),str) or not approval[key].strip() or len(approval[key])>256 for key in ("approved_by","approved_at")): fail("invalid human approval")
    if value["remote_effects"] != {"push_branch":True,"draft_pr":True}: fail("remote effects must explicitly approve branch push and draft PR")
    if raw != canonical(value): fail("request must be canonical JSON")
    core = {key:value[key] for key in sorted(keys-{"operation_id"})}
    expected = "swe-implement-" + hashlib.sha256(canonical(core)).hexdigest()
    if value["operation_id"] != expected: fail("operation ID differs from canonical request")
    return value


def run(command, timeout=120, json_output=False, input_text=None):
    completed = subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=timeout)
    if completed.returncode: fail(f"command failed: {pathlib.Path(command[0]).name}")
    if not json_output: return completed.stdout.strip()
    try: return json.loads(completed.stdout)
    except json.JSONDecodeError as error: raise ValueError("command returned invalid JSON") from error


def verify_sources(request):
    for source in request["source_refs"]:
        ref = source["ref"]
        if not ref.startswith("/"): continue
        if "sha256" not in source: fail("local source requires SHA-256")
        descriptor = os.open(ref, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024: fail("local source is unsafe or oversized")
            digest = hashlib.sha256(stream.read()).hexdigest()
        if digest != source["sha256"]: fail("local source digest differs")


def service_command(args, *command):
    path = "/Users/hermes-agent/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    return ["/usr/bin/sudo","-u",args.service_user,"/usr/bin/env",f"HOME={args.service_home}",f"HERMES_HOME={args.hermes_home}",f"PATH={path}",*map(str,command)]


def slug(title):
    value = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
    return value or "change"


def cache_manifest(args, request):
    owner, repo = request["repository"].split("/"); path = args.cache_root / owner / f"{repo}.json"
    if not path.is_file(): fail("repository cache missing")
    value = json.loads(path.read_text())
    if value.get("repository") != request["repository"] or value.get("default_branch") != request["base_branch"] or value.get("head_sha") != request["base_sha"] or value.get("snapshot_sha") != request["base_sha"]: fail("repository cache identity differs")
    if max(0,int(time.time())-int(value.get("fetched_at",0))) > args.max_cache_age_seconds: fail("repository cache is stale")
    if not pathlib.Path(value.get("snapshot","")).is_dir(): fail("repository snapshot missing")
    return value


def enqueue(args):
    if os.geteuid() != 0: fail("SWE intake requires root host preparation")
    request = load_request(); operation = request["operation_id"]; verify_sources(request)
    authority = run([str(args.authority_bin),"--file",str(args.authority_file),"--check",request["repository"]], json_output=True)
    if authority != {"repo":request["repository"],"granted":True}: fail("repository is not authorized")
    manifest = cache_manifest(args, request); short = operation.removeprefix("swe-implement-")[:12]; branch = f"hermes/{short}-{slug(request['title'])}"
    pin = run([str(args.cache_bin),"--root",str(args.cache_root),"--reader-group",args.reader_group,"pin",request["repository"],operation,request["base_sha"]], json_output=True)
    pin_ref = pin.get("pin_ref")
    workspace = run(service_command(args,args.workspace_bin,"prepare",request["repository"],operation,request["base_sha"],branch,"--mirror",manifest["mirror"],"--pin-ref",pin_ref,"--remote",manifest["remote"],"--work-root",args.work_root), timeout=1800, json_output=True)
    body = canonical({"workflow":"swe-implement","operation":operation,"repository":request["repository"],"base_branch":request["base_branch"],"base_sha":request["base_sha"],"branch":branch,"workspace":workspace["worktree"],"request":request,"evidence_snapshot":manifest["snapshot"],"contract":{"posture":"Caveman reasoning and Ponytail coding","implementation":"smallest complete patch; focused validation; one commit","push":"push only deterministic unprotected branch without force; read back ambiguous result","draft_pr":"open or adopt one marker-bound draft PR; never merge or mark ready","review":"assign draft PR to authenticated agent for discovery and verify exact-head pr-review admission","forbidden":["default/protected push","force-push","merge","deploy","release","package publication","repository administration","credentials","delegation"]}}).decode()
    hcmd = service_command(args,args.hermes_bin)
    boards = run([*hcmd,"kanban","boards","list","--all","--json"],json_output=True)
    board = next((item for item in boards if item.get("slug")==BOARD),None)
    if board is None: run([*hcmd,"kanban","boards","create",BOARD,"--name",BOARD_NAME])
    elif board.get("name") != BOARD_NAME: fail("board name mismatch")
    create = [*hcmd,"kanban","--board",BOARD,"create",request["title"],"--body",body,"--assignee",PROFILE,"--workspace",f"dir:{workspace['worktree']}","--idempotency-key",operation,"--tenant",operation,"--max-runtime","3600","--max-retries","1","--goal","--goal-max-turns","4","--completion-contract","local-only","--created-by","operator","--initial-status","blocked","--json"]
    task = run(create,json_output=True); task_id = task.get("id")
    if not isinstance(task_id,str) or not TASK.fullmatch(task_id): fail("invalid task ID")
    shown = run([*hcmd,"kanban","--board",BOARD,"show",task_id,"--json"],json_output=True); current = shown.get("task",{})
    if current.get("body") != body or current.get("tenant") != operation or current.get("workspace_path") != workspace["worktree"] or current.get("assignee") != PROFILE: fail("task identity drift")
    if current.get("status") == "blocked" and shown.get("runs") == [] and len(shown.get("events",[])) == 2:
        run([*hcmd,"kanban","--board",BOARD,"unblock",task_id]); status = "ready"
    elif current.get("status") in {"ready","running","done","review"}: status = current["status"]
    else: fail("task is blocked or failed; human resolution required")
    return {"board":BOARD,"operation_id":operation,"task_id":task_id,"status":status,"repository":request["repository"],"base_sha":request["base_sha"],"branch":branch,"workspace":workspace["worktree"]}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--authority-file",type=pathlib.Path,required=True); parser.add_argument("--authority-bin",type=pathlib.Path,required=True); parser.add_argument("--cache-root",type=pathlib.Path,required=True); parser.add_argument("--cache-bin",type=pathlib.Path,required=True); parser.add_argument("--workspace-bin",type=pathlib.Path,required=True); parser.add_argument("--work-root",type=pathlib.Path,required=True); parser.add_argument("--reader-group",required=True); parser.add_argument("--service-user",required=True); parser.add_argument("--service-home",type=pathlib.Path,required=True); parser.add_argument("--hermes-home",type=pathlib.Path,required=True); parser.add_argument("--hermes-bin",type=pathlib.Path,required=True); parser.add_argument("--max-cache-age-seconds",type=int,default=3600)
    try: print(json.dumps(enqueue(parser.parse_args()),sort_keys=True,separators=(",",":")))
    except (OSError,ValueError,json.JSONDecodeError,subprocess.TimeoutExpired) as error: raise SystemExit(f"Hermes SWE enqueue failed: {error}")

if __name__=="__main__": main()
