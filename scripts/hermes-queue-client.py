#!/usr/bin/env python3
"""Typed host Hermes intake for the current local account."""
import argparse
import hashlib
import json
import os
import pathlib
import pwd
import re
import subprocess
import sys
import tempfile
from hermes_direct_pr_journal import identity
import hermes_run_request as run_api

DOCUMENTS = {"prd-write": "prd", "design-write": "design", "dd-write": "design", "roadmap-write": "roadmap"}
PR_KINDS = {"pr-review", "pr-maintain", "pr-safety"}
OPERATION = re.compile(r"(prd|design|roadmap|pr-review|pr-maintain|pr-safety)-[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
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
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1 or \
            not isinstance(value.get("kind"), str): fail("unsupported request contract")
    if value["kind"] in PR_KINDS:
        fields = {"version", "kind", "repository", "pr"}
        if value["kind"] != "pr-maintain": fields.add("head_sha")
        if set(value) != fields or not isinstance(value["repository"], str) or \
                not REPO.fullmatch(value["repository"]) or \
                any(part in {".", ".."} for part in value["repository"].split("/")) or \
                type(value["pr"]) is not int or not 0 < value["pr"] <= 2147483647 or \
                ("head_sha" in fields and (not isinstance(value["head_sha"], str) or not SHA.fullmatch(value["head_sha"]))):
            fail("invalid PR request")
        return value
    if set(value) != {"version", "kind", "title", "requirements", "repositories"} or value["kind"] not in DOCUMENTS:
        fail("unsupported request contract")
    if not isinstance(value["title"], str) or not value["title"].strip() or len(value["title"]) > 256 or \
            not isinstance(value["requirements"], str) or not value["requirements"].strip() or \
            len(value["requirements"].encode()) > 2048:
        fail("invalid title or requirements")
    repos = value["repositories"]
    if not isinstance(repos, list) or not 0 < len(repos) <= 5 or any(
        not isinstance(repo, str) or not REPO.fullmatch(repo) or any(part in {".", ".."} for part in repo.split("/"))
        for repo in repos) or len(repos) != len(set(repos)):
        fail("invalid repositories")
    return value


def host():
    if os.geteuid() == 0 or os.geteuid() != os.getuid():
        fail("run as the current non-root account; do not use sudo")
    home = pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)
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
    board = "pr-safety-council" if operation.startswith("pr-safety-") else \
        "pr-maintain" if operation.startswith("pr-maintain-") else \
        "pr-review" if operation.startswith("pr-review-") else next(
            board for board, prefix in DOCUMENTS.items() if operation.startswith(prefix + "-"))
    tenant = operation
    if board == "pr-safety-council":
        nonce = hashlib.sha256(f"direct:{operation}".encode()).hexdigest()[:32]
        tenant = "pr-risk-council-" + hashlib.sha256(f"{operation}:{nonce}".encode()).hexdigest()[:32]
    tasks = run([str(binary), "kanban", "--board", board, "list", "--tenant", tenant, "--archived", "--json"], env)
    if not isinstance(tasks, list) or not 0 < len(tasks) <= 100: fail("operation missing or ambiguous")
    by_stage = {}
    for task in tasks:
        if not isinstance(task, dict) or task.get("tenant") != tenant or \
                not isinstance(task.get("id"), str) or not isinstance(task.get("status"), str):
            fail("Kanban task mismatch")
        try: body = json.loads(task["body"], object_pairs_hook=unique)
        except (KeyError, ValueError, TypeError) as error: raise ValueError("invalid Kanban task body") from error
        if not isinstance(body, dict): fail("invalid Kanban task body")
        if board in {"pr-review", "pr-maintain"}:
            try:
                expected = identity(board, body["repo"], body["number"], body["head_sha"],
                                    body.get("feedback_digest"))["operation_id"]
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("invalid PR task identity") from error
            if body.get("operation_id") != operation or expected != operation: fail("PR task identity mismatch")
            stage = "card"
        elif board == "pr-safety-council":
            if body.get("workflow_id") != tenant or not isinstance(body.get("role"), str): fail("safety graph mismatch")
            stage = body["role"]
        else:
            if body.get("workflow") != board or body.get("operation") != operation or \
                    type(body.get("round")) is not int or body["round"] not in range(3) or \
                    not isinstance(body.get("stage"), str): fail("document task mismatch")
            stage = f"{body['round']}:{body['stage']}"
        if stage in by_stage and (board in {"pr-review", "pr-maintain", "pr-safety-council"} or stage.endswith(":synthesis")):
            fail("duplicate Kanban task")
        by_stage[stage] = task
    if board in {"pr-review", "pr-maintain"}:
        if len(tasks) != 1: fail("ambiguous PR card")
        current = tasks[0]
    elif board == "pr-safety-council":
        if len(tasks) != 5 or set(by_stage) != {"review", "security", "reliability", "architecture", "synthesis"}:
            fail("incomplete PR safety graph")
        current = by_stage["synthesis"]
    else:
        if "0:writer" not in by_stage: fail("document writer missing")
        round_ = max(int(key.split(":", 1)[0]) for key in by_stage)
        current = by_stage.get(f"{round_}:synthesis", by_stage.get(f"{round_}:writer"))
        if current is None: fail("current document task missing")
    return {"version": 1, "kind": "pr-safety" if board == "pr-safety-council" else board,
            "operation_id": operation, "board": board, "task_id": current["id"], "status": current["status"]}


def authorized(repo, env):
    check = subprocess.run([sys.executable, str(ROOT / "hermes-authority.py"), "--file",
                            "/usr/local/etc/ai-pr-automation/authority.yaml", "--check", repo],
                           capture_output=True, env=env, timeout=5)
    if check.returncode: fail(f"repository not granted: {repo}")


def github(args, env, allow_failure=False):
    try: result = subprocess.run(["gh", *args], capture_output=True, timeout=45,
                                 env={**env, "GH_PROMPT_DISABLED": "1"})
    except (OSError, subprocess.TimeoutExpired) as error: raise ValueError("GitHub lookup unavailable") from error
    if len(result.stdout) > 1024 * 1024 or result.returncode and (not allow_failure or not result.stdout):
        fail("GitHub lookup failed")
    try:
        decoder = json.JSONDecoder(object_pairs_hook=unique)
        text = result.stdout.decode("utf-8"); values = []
        while text.strip():
            item, end = decoder.raw_decode(text.lstrip()); values.append(item)
            text = text.lstrip()[end:]
        return values if len(values) != 1 else values[0]
    except (UnicodeError, ValueError) as error: raise ValueError("invalid GitHub response") from error


def feedback(repo, number, env):
    user = github(["api", "user"], env)
    login = user.get("login") if isinstance(user, dict) else None
    if not isinstance(login, str) or not login: fail("GitHub identity unavailable")
    reviews = github(["api", "--paginate", f"repos/{repo}/pulls/{number}/reviews?per_page=100"], env)
    query = ('query($owner:String!,$name:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$name)'
             '{pullRequest(number:$number){reviewThreads(first:100,after:$endCursor){nodes{id isResolved isOutdated '
             'comments(last:100){nodes{id databaseId author{login} body createdAt updatedAt}}} '
             'pageInfo{hasNextPage endCursor}}}}}')
    owner, name = repo.split("/")
    threads = github(["api", "graphql", "--paginate", "-F", f"owner={owner}", "-F", f"name={name}",
                      "-F", f"number={number}", "-f", f"query={query}"], env)
    checks = github(["pr", "checks", str(number), "-R", repo, "--json", "name,bucket,state,link"], env, True)
    if not isinstance(reviews, list) or not isinstance(checks, list): fail("invalid PR feedback snapshot")
    reviews = [item for page in reviews for item in (page if isinstance(page, list) else [page])]
    pages = threads if isinstance(threads, list) else [threads]
    nodes = []
    for page in pages:
        try: details = page["data"]["repository"]["pullRequest"]["reviewThreads"]
        except (KeyError, TypeError) as error: raise ValueError("invalid PR review threads") from error
        if not isinstance(details.get("nodes"), list): fail("invalid PR review threads")
        nodes.extend(details["nodes"])
    if pages and pages[-1]["data"]["repository"]["pullRequest"]["reviewThreads"]["pageInfo"]["hasNextPage"]:
        fail("incomplete PR review threads")
    items = set()
    for review in reviews:
        if isinstance(review, dict) and isinstance(review.get("user"), dict) and \
                isinstance(review["user"].get("login"), str) and \
                review["user"]["login"].lower() != login.lower() and \
                isinstance(review.get("body"), str) and review["body"].strip() and review.get("id") is not None:
            items.add(("review", str(review["id"]), hashlib.sha256(review["body"].encode()).hexdigest()))
    for thread in nodes:
        if not isinstance(thread, dict) or thread.get("isResolved") is not False or thread.get("isOutdated") is not False:
            continue
        comments = thread.get("comments", {}).get("nodes", [])
        if not isinstance(thread.get("id"), str) or not isinstance(comments, list): fail("invalid PR feedback thread")
        for comment in comments:
            if isinstance(comment, dict) and isinstance(comment.get("author"), dict) and \
                    isinstance(comment["author"].get("login"), str) and \
                    comment["author"]["login"].lower() != login.lower() and \
                    isinstance(comment.get("body"), str) and comment["body"].strip() and isinstance(comment.get("id"), str):
                items.add(("thread", thread["id"], comment["id"], hashlib.sha256(comment["body"].encode()).hexdigest()))
    for check in checks:
        if isinstance(check, dict) and check.get("bucket") == "fail":
            selected = {key: check.get(key) for key in ("name", "bucket", "state", "link")}
            items.add(("check", hashlib.sha256(canonical(selected)).hexdigest()))
    if not items: fail("no actionable PR feedback")
    return hashlib.sha256(canonical([list(item) for item in sorted(items)])).hexdigest()


def profile_key(hermes_home, kind):
    try:
        lines = (hermes_home / "profiles" / run_api.PROFILES[kind] / ".env").read_text().splitlines()
    except OSError as error:
        raise ValueError("Hermes profile API key unavailable") from error
    keys = [line.partition("=")[2] for line in lines if line.startswith("API_SERVER_KEY=")]
    if len(keys) != 1 or not keys[0]: fail("Hermes profile API key unavailable")
    return keys[0]


def pr_request(value, hermes_home, binary, env):
    kind, repo, number = value["kind"], value["repository"], value["pr"]
    authorized(repo, env)
    info = github(["pr", "view", str(number), "-R", repo, "--json", "number,title,url,headRefOid,state"], env)
    if not isinstance(info, dict) or info.get("number") != number or info.get("state") != "OPEN" or \
            info.get("url") != f"https://github.com/{repo}/pull/{number}" or \
            not isinstance(info.get("title"), str) or not 0 < len(info["title"]) <= 256 or \
            not isinstance(info.get("headRefOid"), str) or not SHA.fullmatch(info["headRefOid"]):
        fail("PR metadata invalid or PR is not open")
    head = info["headRefOid"]
    if kind == "pr-review" and head != value["head_sha"]: fail("PR head changed")
    digest = feedback(repo, number, env) if kind == "pr-maintain" else None
    result = run_api.submit(kind, repo, number, head, profile_key(hermes_home, kind),
                            feedback_digest=digest)
    return {"version": 1, "kind": kind, **result}


def safety_request(value, binary, env):
    repo, number, head = value["repository"], value["pr"], value["head_sha"]
    authorized(repo, env)
    info = github(["api", f"repos/{repo}/pulls/{number}"], env)
    authors = {name.strip().lower() for name in os.environ.get("PR_SAFETY_MERGED_PR_AUTHORS", "").split(",") if name.strip()}
    if not isinstance(info, dict) or info.get("state") != "closed" or not isinstance(info.get("merged_at"), str) or \
            info.get("merge_commit_sha") != head or not isinstance(info.get("base"), dict) or \
            not isinstance(info["base"].get("sha"), str) or not SHA.fullmatch(info["base"]["sha"]) or \
            not isinstance(info.get("user"), dict) or str(info["user"].get("login", "")).lower() not in authors:
        fail("PR safety requires an enrolled author's exact merged head")
    operation = "pr-safety-" + hashlib.sha256(f"{repo}#{number}@{head}".encode()).hexdigest()
    settings = ("PR_SAFETY_MERGED_PR_AUTHORS", "PR_SAFETY_SNAPSHOT_ROOT", "PR_SAFETY_POLICY_ROOT",
                "PR_SAFETY_POLICY_PATH", "PR_SAFETY_POLICY_VERSION", "PR_SAFETY_POLICY_DIGEST")
    if any(not os.environ.get(key) for key in settings): fail("host PR safety settings unavailable")
    with tempfile.TemporaryDirectory() as directory:
        record = pathlib.Path(directory) / "record.jsonl"
        record.write_bytes(canonical({"repo": repo, "number": number, "mergeSha": head,
                                      "baseSha": info["base"]["sha"]}) + b"\n")
        producer_env = {**env, **{key: os.environ[key] for key in settings},
                        "PR_SAFETY_QUEUE_ENGINE": "kanban", "PR_SAFETY_MERGED_PR_INPUT_FILE": str(record),
                        "HERMES_BIN": str(binary)}
        producer = ROOT / "hermes-pr-safety-producer"
        if not producer.is_file(): producer = ROOT.parent / "bin/hermes-pr-safety-producer"
        try: outcome = subprocess.run([str(producer)], capture_output=True, env=producer_env, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as error: raise ValueError("PR safety admission uncertain") from error
        if outcome.returncode: fail("PR safety admission failed; inspect host logs")
    return status(operation, binary, env)


def request(value, hermes_home, binary, env):
    if value["kind"] == "pr-safety": return safety_request(value, binary, env)
    if value["kind"] in {"pr-review", "pr-maintain"}: return pr_request(value, hermes_home, binary, env)
    for repo in value["repositories"]: authorized(repo, env)
    kind = value["kind"]
    board = "design-write" if kind == "dd-write" else kind
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
    if not isinstance(result, dict) or result.get("board") != board or result.get("operation_id") != operation or \
            not isinstance(result.get("tasks"), dict) or not isinstance(result["tasks"].get("writer"), str):
        fail("ambiguous Hermes admission response")
    return status(operation, binary, env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("request")
    show = actions.add_parser("status-check")
    show.add_argument("kind", choices=[*DOCUMENTS, *sorted(PR_KINDS)])
    show.add_argument("identifier", help="PR run ID or Kanban operation ID for other kinds")
    args = parser.parse_args()
    try:
        _, hermes_home, binary, env = host()
        if args.action == "request":
            output = request(intake(sys.stdin.buffer.read(8193)), hermes_home, binary, env)
        else:
            if args.kind in run_api.PROFILES:
                output = {"version": 1, "kind": args.kind, **run_api.status(
                    args.kind, args.identifier, profile_key(hermes_home, args.kind))}
            else:
                output = status(args.identifier, binary, env)
                if output["kind"] != ("design-write" if args.kind == "dd-write" else args.kind):
                    fail("operation does not belong to requested board")
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"Hermes queue client: {error}")


if __name__ == "__main__": main()
