#!/usr/bin/env python3
"""Pure, head-bound plan for a pre-merge Hermes Kanban review council."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

from hermes_direct_pr_journal import identity


PROFILES = {"generalist": "pr-review-generalist-v2",
            "reliability": "pr-review-reliability-v2",
            "mvp": "pr-review-mvp-v2"}
SECURITY = "pr-review-security-v2"
MODELS = {role: "gpt-6-sol" for role in ("generalist", "reliability", "mvp", "security", "synthesis")}
BOARD = "pr-review"
SECURITY_PATH = re.compile(r"(^|[/_.-])(auth|authentication|secret|secrets|credential|credentials|iam|crypto|cryptography|permission|permissions)([/_.-]|$)", re.I)
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def local_diff(path, base_sha, head_sha):
    if not path or not HEX40.fullmatch(base_sha) or not HEX40.fullmatch(head_sha):
        raise ValueError("local review commits are invalid")
    completed = subprocess.run(["git", "-C", str(path), "diff", "--no-ext-diff", "--no-textconv",
                                "--binary", f"{base_sha}...{head_sha}", "--"],
                               capture_output=True, timeout=30,
                               env={**{key:value for key,value in os.environ.items() if not key.startswith("GIT_")},
                                    "GIT_NO_REPLACE_OBJECTS":"1", "GIT_TERMINAL_PROMPT":"0"})
    if completed.returncode or not 0 < len(completed.stdout) <= 262144:
        raise ValueError("local PR diff is unavailable or too large")
    return completed.stdout


def local_changed_paths(path, base_sha, head_sha):
    result = subprocess.run(["git", "-C", str(path), "diff", "--no-ext-diff", "--no-textconv",
                             "--name-only", "-z", f"{base_sha}...{head_sha}", "--"],
                            capture_output=True, timeout=30,
                            env={**{key:value for key,value in os.environ.items() if not key.startswith("GIT_")},
                                 "GIT_NO_REPLACE_OBJECTS":"1", "GIT_TERMINAL_PROMPT":"0"})
    if result.returncode or not result.stdout.endswith(b"\0") or len(result.stdout) > 65536:
        raise ValueError("local PR path inventory is unavailable")
    try:
        return [name.decode("utf-8") for name in result.stdout[:-1].split(b"\0")]
    except UnicodeDecodeError as error:
        raise ValueError("local PR path inventory is not UTF-8") from error


def collect(github, repo, number, head_sha, repository_path=None):
    """Pin one pre-merge head; use local Git objects when a checkout is supplied."""
    before = github.pr(repo, number)
    if (not isinstance(before, dict) or before.get("state") != "open"
            or before.get("head_sha") != head_sha
            or not isinstance(before.get("base_sha"), str) or not HEX40.fullmatch(before["base_sha"])
            or type(before.get("changed_files")) is not int or not 0 < before["changed_files"] <= 250):
        raise ValueError("PR head or diff inventory cannot be pinned")
    files = github.files(repo, number)
    diff = local_diff(repository_path, before["base_sha"], head_sha) if repository_path else github.diff(repo, number)
    if (not isinstance(files, list) or len(files) != before["changed_files"]
            or not isinstance(diff, bytes) or not 0 < len(diff) <= 262144):
        raise ValueError("incomplete or oversized PR diff")
    paths = []
    for item in files:
        if (not isinstance(item, dict) or not isinstance(item.get("filename"), str)
                or not isinstance(item.get("patch"), str) or not item["patch"]):
            raise ValueError("unreviewable PR file or missing patch")
        paths.append(item["filename"])
        if not repository_path and item["filename"].encode() not in diff:
            raise ValueError("PR diff does not contain its file inventory")
    if len(set(paths)) != len(paths) or not diff.startswith(b"diff --git "):
        raise ValueError("PR diff and file inventory disagree")
    if repository_path:
        if sorted(local_changed_paths(repository_path, before["base_sha"], head_sha)) != sorted(paths):
            raise ValueError("local Git and GitHub changed-file inventories disagree")
    elif diff.count(b"diff --git ") != len(paths):
        raise ValueError("PR diff and file inventory disagree")
    reviews, comments, checks = github.reviews(repo, number), github.comments(repo, number), github.checks(repo, head_sha)
    if any(not isinstance(value, list) or len(value) > 500 for value in (reviews, comments, checks)):
        raise ValueError("PR context inventory is incomplete")
    context = json.dumps({"intent": before.get("body") or "", "prior_feedback": reviews + comments,
                          "checks": checks}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(context) > 65536 or github.pr(repo, number) != before:
        raise ValueError("PR context is oversized or head changed while reading")
    request = {"repo": repo, "number": number, "head_sha": head_sha,
               "base_sha": before["base_sha"], "diff_digest": hashlib.sha256(diff).hexdigest(),
               "context_digest": hashlib.sha256(context).hexdigest(), "changed_paths": paths}
    if repository_path:
        request["repository_path"] = str(repository_path)
    request["operation_id"] = identity("pr-review", repo, number, head_sha)["operation_id"]
    plan(request)
    return request, diff, context


def plan(request):
    required = {"operation_id", "repo", "number", "head_sha", "base_sha", "diff_digest", "context_digest", "changed_paths"}
    if not isinstance(request, dict) or set(request) not in (required, required | {"repository_path"}):
        raise ValueError("pre-merge council request shape changed")
    try:
        operation = identity("pr-review", request["repo"], request["number"], request["head_sha"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("pre-merge council operation identity changed") from error
    paths = request["changed_paths"]
    if (request["operation_id"] != operation["operation_id"]
            or not isinstance(request["base_sha"], str) or not HEX40.fullmatch(request["base_sha"])
            or not isinstance(request["diff_digest"], str) or not HEX64.fullmatch(request["diff_digest"])
            or not isinstance(request["context_digest"], str) or not HEX64.fullmatch(request["context_digest"])
            or not isinstance(paths, list) or not 0 < len(paths) <= 250
            or ("repository_path" in request and
                (not isinstance(request["repository_path"], str) or not Path(request["repository_path"]).is_absolute()))):
        raise ValueError("pre-merge council snapshot identity is incomplete")
    for path in paths:
        if (not isinstance(path, str) or not 0 < len(path) <= 512 or "\x00" in path
                or PurePosixPath(path).is_absolute() or any(part in (".", "..") for part in PurePosixPath(path).parts)):
            raise ValueError("unsafe changed path")
    specialists = dict(PROFILES)
    if any(SECURITY_PATH.search(path) for path in paths):
        specialists["security"] = SECURITY
    binding = {"operation_id": operation["operation_id"], "base_sha": request["base_sha"],
               "head_sha": request["head_sha"], "diff_digest": request["diff_digest"],
               "context_digest": request["context_digest"], "specialists": list(specialists), "changed_paths": sorted(paths)}
    if "repository_path" in request:
        binding["repository_path"] = request["repository_path"]
    artifact_digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"operation_id": operation["operation_id"], "artifact_digest": artifact_digest,
            "head_sha": request["head_sha"], "base_sha": request["base_sha"],
            "diff_digest": request["diff_digest"], "context_digest": request["context_digest"],
            **({"repository_path": request["repository_path"]} if "repository_path" in request else {}),
            "workflow_id": "pr-review-council-" + artifact_digest[:32],
            "specialists": specialists, "changed_paths": paths,
            "synthesis": {"profile": "pr-review-synthesis-v2", "parents": list(specialists)},
            "limits": {"deadline_seconds": 480, "max_active_heads": 2}}


def queue(enqueue, work_root, request):
    """Fence a head without GitHub I/O or a Kanban task in the ingress process."""
    root = enqueue.safe_dir(work_root)
    workspace = enqueue.safe_dir(root / request["operation_id"], True)
    owner = enqueue.canonical({"route": "council-v2", "operation_id": request["operation_id"],
                               "repo": request["repo"], "number": request["number"],
                               "head_sha": request["head_sha"], "title": request["title"]})
    path = workspace / "request.json"
    try:
        previous = enqueue.read_immutable(path)
    except FileNotFoundError:
        if any(workspace.iterdir()):
            raise ValueError("ambiguous prior review operation")
        enqueue.immutable(path, owner)
        return "council"
    if previous == owner:
        return "council"
    try:
        value = json.loads(previous)
    except (ValueError, UnicodeError) as error:
        raise ValueError("review owner record is unreadable") from error
    if not isinstance(value, dict) or previous != enqueue.canonical(value) \
            or any(value.get(key) != request[key] for key in ("operation_id", "repo", "number", "head_sha")):
        raise ValueError("review operation already has a different owner")
    if set(value) == {"route", "operation_id", "repo", "number", "head_sha", "title"} \
            and value["route"] == "council-v2":
        return "council"
    if set(value) != set(request) or any(value[key] != request[key] for key in request if key != "title"):
        raise ValueError("review operation already has a different owner")
    return "legacy"


def claim(enqueue, work_root, spec, request):
    """Bind a prepared snapshot to the already-fenced review head."""
    if (not isinstance(request, dict) or set(request) !=
            {"operation_id", "repo", "number", "url", "title", "head_sha"}
            or request["operation_id"] != spec["operation_id"]
            or request["head_sha"] != spec["head_sha"]
            or identity("pr-review", request["repo"], request["number"], request["head_sha"])["operation_id"] != spec["operation_id"]):
        raise ValueError("review council owner identity differs")
    route = queue(enqueue, work_root, request)
    if route == "council":
        manifest = Path(work_root) / spec["operation_id"] / "input/identity.json"
        if manifest.exists() or manifest.is_symlink():
            previous = json.loads(_immutable_file(manifest))
            if previous["artifact_digest"] != spec["artifact_digest"]:
                raise ValueError("review snapshot differs from pinned owner")
    return route


def admit(kb, conn, enqueue, github, work_root, request, repository_path=None):
    """Admit a new head or replay only its recorded legacy/council route."""
    root = enqueue.safe_dir(work_root)
    workspace = root / request["operation_id"]
    owner_path = workspace / "request.json"
    if owner_path.exists() or owner_path.is_symlink():
        enqueue.safe_dir(workspace)
        previous = json.loads(enqueue.read_immutable(owner_path))
        if isinstance(previous, dict) and set(previous) == set(request):
            if any(previous[key] != request[key] for key in request if key != "title"):
                raise ValueError("legacy review owner identity changed")
            return {"route": "legacy"}
        manifest_path = workspace / "input/identity.json"
        if not manifest_path.exists() and not manifest_path.is_symlink():
            outcome = json.loads(_immutable_file(workspace / "review-outcome.json"))
            if (not isinstance(previous, dict) or set(previous) !=
                    {"route", "operation_id", "repo", "number", "head_sha", "title"}
                    or previous["route"] != "council-v2"
                    or any(previous[key] != request[key] for key in ("operation_id", "repo", "number", "head_sha"))
                    or not isinstance(outcome, dict) or set(outcome) not in (
                    {"status", "operation_id", "artifact_digest", "head_sha", "base_sha",
                     "diff_digest", "context_digest", "task_id", "review_id"},
                    {"status", "operation_id", "artifact_digest", "head_sha", "base_sha",
                     "diff_digest", "context_digest", "task_id", "review_id", "repository_path"})
                    or outcome["status"] != "verified"
                    or any(outcome[key] != previous[key] for key in ("operation_id", "head_sha"))
                    or not isinstance(outcome["task_id"], str) or not re.fullmatch(r"t_[0-9a-f]{8}", outcome["task_id"])
                    or type(outcome["review_id"]) is not int or outcome["review_id"] <= 0):
                raise ValueError("review completion receipt differs from owner")
            if any((workspace / name).exists() or (workspace / name).is_symlink()
                   for name in ("snapshot/diff.patch", "input/context.json")):
                raise ValueError("verified review cleanup incomplete; reconcile private snapshot")
            return {"route": "council", "task_id": outcome["task_id"],
                    "operation_id": request["operation_id"], "status": "done"}
        manifest = json.loads(_immutable_file(manifest_path))
        spec = plan({"operation_id": request["operation_id"], "repo": request["repo"],
                     "number": request["number"], "head_sha": request["head_sha"],
                     "base_sha": manifest["base_sha"], "diff_digest": manifest["diff_digest"],
                     "context_digest": manifest["context_digest"], "changed_paths": manifest["changed_paths"],
                     **({"repository_path": manifest["repository_path"]} if "repository_path" in manifest else {})})
        if repository_path and spec.get("repository_path") != str(repository_path):
            raise ValueError("local review checkout changed on replay")
        claim(enqueue, root, spec, request)
    else:
        snapshot, diff, context = collect(github, request["repo"], request["number"], request["head_sha"],
                                          repository_path)
        spec = plan(snapshot)
        if claim(enqueue, root, spec, request) == "legacy":
            return {"route": "legacy"}
        prepare_snapshot(workspace, spec, diff, context)
    tasks = setup(kb, conn, spec, workspace)
    return {"route": "council", "task_id": tasks["synthesis"], "operation_id": spec["operation_id"]}


def handoffs(kb, conn, spec, tasks):
    expected = {**spec["specialists"], "synthesis": spec["synthesis"]["profile"]}
    if set(tasks) != set(expected):
        raise ValueError("council task roster differs")
    outputs = {}
    for role, profile in expected.items():
        task = kb.get_task(conn, tasks[role])
        if task is None or task.assignee != profile:
            raise ValueError("council task profile differs")
        if task.status != "done":
            return None
        runs = kb.list_runs(conn, tasks[role])
        if len(runs) != 1 or runs[0].outcome != "completed" or runs[0].profile != profile:
            raise ValueError("council task run is missing or ambiguous")
        outputs[role] = runs[0].metadata
    check_verdict(spec, {role: outputs[role] for role in spec["specialists"]}, outputs["synthesis"])
    return outputs


def canonical_finding(spec, finding):
    if not isinstance(finding, dict):
        raise ValueError("invalid council finding")
    value = dict(finding)
    path = value.get("path")
    if isinstance(path, str) and path not in spec["changed_paths"] and path.startswith("snapshot/"):
        value["path"] = path.removeprefix("snapshot/")
    evidence = value.get("evidence")
    if isinstance(evidence, str) and len(evidence) <= 2000:
        value["evidence"] = evidence.replace('\\"', '"')
    return value


def _valid_finding(spec, finding):
    if (not isinstance(finding, dict) or set(finding) != {"severity", "required", "path", "line", "claim", "evidence", "suggestion"}
            or finding["severity"] not in ("blocker", "major", "minor", "nit")
            or type(finding["required"]) is not bool or type(finding["line"]) is not int
            or finding["line"] <= 0 or finding["path"] not in spec["changed_paths"]
            or any(not isinstance(finding[key], str) or not finding[key].strip()
                   or len(finding[key]) > limit for key, limit in
                   (("claim", 512), ("evidence", 2000), ("suggestion", 1024)))
            or (finding["severity"] in ("blocker", "major") and not finding["required"])
            or (finding["severity"] == "nit" and finding["required"])):
        raise ValueError("invalid council changed-line finding")
    return finding["required"]


def check_verdict(spec, outputs, synthesis):
    """Accept only a typed synthesis that preserves every specialist finding."""
    keys = {"operation_id", "artifact_digest", "role", "verdict", "findings"}
    if not isinstance(outputs, dict) or set(outputs) != set(spec["specialists"]) \
            or not isinstance(synthesis, dict) or set(synthesis) != keys \
            or synthesis["role"] != "synthesis" or synthesis["operation_id"] != spec["operation_id"] \
            or synthesis["artifact_digest"] != spec["artifact_digest"] \
            or synthesis["verdict"] not in ("approve", "request-changes", "needs-info") \
            or not isinstance(synthesis["findings"], list) or len(synthesis["findings"]) > 50:
        raise ValueError("council synthesis differs from contract")
    needed, needs_info, required = set(), False, False
    for role, item in outputs.items():
        if not isinstance(item, dict) or set(item) != keys \
                or item["operation_id"] != spec["operation_id"] or item["artifact_digest"] != spec["artifact_digest"] \
                or item["role"] != role or item["verdict"] not in ("clear", "findings", "needs-info") \
                or not isinstance(item["findings"], list) or len(item["findings"]) > 25:
            raise ValueError("invalid council specialist output")
        needs_info |= item["verdict"] == "needs-info"
        for finding in item["findings"]:
            normalized = canonical_finding(spec, finding)
            required |= _valid_finding(spec, normalized)
            needed.add(json.dumps(normalized, sort_keys=True, separators=(",", ":")))
    included = set()
    for finding in synthesis["findings"]:
        normalized = canonical_finding(spec, finding)
        required |= _valid_finding(spec, normalized)
        included.add(json.dumps(normalized, sort_keys=True, separators=(",", ":")))
    if not needed <= included:
        raise ValueError("synthesis omitted a specialist finding")
    verdict = synthesis["verdict"]
    if verdict == "approve" and (required or needs_info) or verdict == "request-changes" and not required:
        raise ValueError("council verdict ignores required changes or missing evidence")
    return verdict


def _private_dir(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("unsafe council directory")
    info = path.lstat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("council directory must be owner-only")


def _immutable_file(path, value=None, *, mode=0o400, max_bytes=262144):
    path = Path(path)
    if value is not None:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as output:
                output.write(value); output.flush(); os.fsync(output.fileno())
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != mode or info.st_size > max_bytes):
            raise ValueError("unsafe council snapshot file")
        data = source.read(max_bytes + 1)
    if value is not None and data != value:
        raise ValueError("existing council snapshot differs")
    return data


def prepare_snapshot(workspace, spec, diff, context):
    root = Path(workspace)
    if (not root.is_absolute() or not isinstance(diff, bytes) or len(diff) > 262144
            or hashlib.sha256(diff).hexdigest() != spec["diff_digest"]
            or not isinstance(context, bytes) or len(context) > 65536
            or hashlib.sha256(context).hexdigest() != spec["context_digest"]):
        raise ValueError("incomplete or oversized council diff")
    root.mkdir(mode=0o700, exist_ok=True)
    _private_dir(root)
    for name in (("input",) if spec.get("repository_path") else ("input", "snapshot")):
        directory = root / name
        directory.mkdir(mode=0o700, exist_ok=True)
        _private_dir(directory)
    manifest = {key: spec[key] for key in
                ("operation_id", "head_sha", "base_sha", "diff_digest", "context_digest", "artifact_digest", "changed_paths")}
    if spec.get("repository_path"):
        manifest["repository_path"] = spec["repository_path"]
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > 65536:
        raise ValueError("council identity exceeds limit")
    if not spec.get("repository_path"):
        _immutable_file(root / "snapshot/diff.patch", diff)
    _immutable_file(root / "input/context.json", context)
    _immutable_file(root / "input/identity.json", encoded)
    _verified_snapshot(root, spec)


def _verified_snapshot(workspace, spec):
    root = Path(workspace)
    if not root.is_absolute():
        raise ValueError("council snapshot workspace must be absolute")
    for directory in ((root, root / "input") if spec.get("repository_path") else
                      (root, root / "input", root / "snapshot")):
        _private_dir(directory)
    path = root / "input/identity.json"
    wanted = {key: spec[key] for key in
              ("operation_id", "head_sha", "base_sha", "diff_digest", "context_digest", "artifact_digest", "changed_paths")}
    if spec.get("repository_path"):
        wanted["repository_path"] = spec["repository_path"]
    try:
        actual = json.loads(_immutable_file(path))
        diff = (local_diff(spec["repository_path"], spec["base_sha"], spec["head_sha"])
                if spec.get("repository_path") else _immutable_file(root / "snapshot/diff.patch"))
        context = _immutable_file(root / "input/context.json")
    except (OSError, ValueError) as error:
        raise ValueError("council snapshot identity is unreadable") from error
    if (actual != wanted or hashlib.sha256(diff).hexdigest() != spec["diff_digest"]
            or hashlib.sha256(context).hexdigest() != spec["context_digest"]):
        raise ValueError("council snapshot content differs from contract")


def setup(kb, conn, spec, workspace):
    """Create/adopt the exact effect-free specialist graph; never publish a review."""
    _verified_snapshot(workspace, spec)
    root = Path(workspace)
    workspaces = root / "workspaces"
    workspaces.mkdir(mode=0o700, exist_ok=True)
    _private_dir(workspaces)
    tasks = {}
    for role, profile in [*spec["specialists"].items(), ("synthesis", spec["synthesis"]["profile"])]:
        role_workspace = workspaces / role
        role_workspace.mkdir(mode=0o700, exist_ok=True)
        _private_dir(role_workspace)
        local = bool(spec.get("repository_path"))
        binding = json.dumps({"schema_version": 2 if local else 1,
                              "snapshot_root": spec["repository_path"] if local else str(root / "snapshot"),
                              "input_root": str(root / "input"),
                              **({"head_sha": spec["head_sha"], "base_sha": spec["base_sha"]} if local else {})},
                             sort_keys=True, separators=(",", ":")).encode()
        _immutable_file(role_workspace / ".council-tools.json", binding, mode=0o440, max_bytes=4096)
        parents = list(tasks.values()) if role == "synthesis" else []
        key = f'{spec["workflow_id"]}:{role}'
        body = json.dumps({"workflow_id": spec["workflow_id"], "artifact_digest": spec["artifact_digest"],
                           "operation_id": spec["operation_id"], "role": role,
                           "snapshot": "snapshot/", "parents": parents,
                           **({"repository_path": spec["repository_path"], "head_sha": spec["head_sha"]}
                              if local else {})}, sort_keys=True, separators=(",", ":"))
        existing = conn.execute("SELECT id,assignee,body FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
        if existing:
            linked = [row[0] for row in conn.execute("SELECT parent_id FROM task_links WHERE child_id=? ORDER BY parent_id", (existing["id"],))]
            if existing["assignee"] != profile or existing["body"] != body or linked != sorted(parents):
                raise ValueError("existing council card differs from immutable graph")
            tasks[role] = existing["id"]
        else:
            tasks[role] = kb.create_task(
                conn, title=f"PR review council: {role}", body=body, assignee=profile,
                created_by="operator", workspace_kind="dir", workspace_path=str(role_workspace),
                idempotency_key=key, parents=parents, board=BOARD, max_runtime_seconds=480,
                max_retries=0, model_override=MODELS[role], provider_override="openai-codex",
                goal_mode=False, completion_contract="local-only",
            )
    return tasks
