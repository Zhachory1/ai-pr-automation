#!/usr/bin/env python3
"""One fenced exact-head GitHub review effect for a validated council package."""

from contextlib import closing
import hashlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from hermes_direct_pr_journal import identity


SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
EVENTS = {"APPROVE", "REQUEST_CHANGES", "COMMENT"}
LOADER = SourceFileLoader("hermes_pr_review_council", str(Path(__file__).with_name("hermes-pr-review-council.py")))
SPEC = spec_from_loader(LOADER.name, LOADER)
COUNCIL = module_from_spec(SPEC)
LOADER.exec_module(COUNCIL)


def _db(path):
    path = Path(path)
    root = path.parent
    info = root.lstat()
    if root.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() \
            or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("review effect directory must be owner-only")
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = None
    if fd is not None:
        os.close(fd)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() \
            or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("review effect ledger must be owner-only")
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("""CREATE TABLE IF NOT EXISTS effects (
        operation_id TEXT PRIMARY KEY, repo TEXT NOT NULL, pr INTEGER NOT NULL,
        head_sha TEXT NOT NULL, base_sha TEXT NOT NULL, artifact_digest TEXT NOT NULL,
        body_digest TEXT NOT NULL, event TEXT NOT NULL, phase TEXT NOT NULL, review_id INTEGER
    )""")
    return conn


def purge_verified(path, root, spec, task_id):
    """Drop private review content only after a durable verified effect receipt."""
    if not Path(path).exists() or not isinstance(task_id, str) or not re.fullmatch(r"t_[0-9a-f]{8}", task_id):
        raise ValueError("review effect is not verified")
    with closing(_db(path)) as conn:
        row = conn.execute("SELECT phase,artifact_digest,head_sha,review_id FROM effects WHERE operation_id=?",
                           (spec["operation_id"],)).fetchone()
        if not row or tuple(row)[:3] != ("verified", spec["artifact_digest"], spec["head_sha"]) \
                or type(row["review_id"]) is not int or row["review_id"] <= 0:
            raise ValueError("review effect is not verified")
    root = Path(root)
    for directory in ((root, root / "input") if spec.get("repository_path") else
                      (root, root / "input", root / "snapshot")):
        COUNCIL._private_dir(directory)
    removable = []
    for name, digest in (("input/context.json", spec["context_digest"]),) if spec.get("repository_path") else \
                        (("snapshot/diff.patch", spec["diff_digest"]), ("input/context.json", spec["context_digest"])):
        candidate = root / name
        if candidate.exists() or candidate.is_symlink():
            if hashlib.sha256(COUNCIL._immutable_file(candidate)).hexdigest() != digest:
                raise ValueError("private snapshot differs before removal")
            removable.append(candidate)
    candidate = root / "input/identity.json"
    if candidate.exists() or candidate.is_symlink():
        value = json.loads(COUNCIL._immutable_file(candidate))
        if value.get("operation_id") != spec["operation_id"] or value.get("artifact_digest") != spec["artifact_digest"]:
            raise ValueError("private snapshot identity differs before removal")
        removable.append(candidate)
    receipt = {"status": "verified", "operation_id": spec["operation_id"],
               "artifact_digest": spec["artifact_digest"], "head_sha": spec["head_sha"],
               "base_sha": spec["base_sha"], "diff_digest": spec["diff_digest"],
               "context_digest": spec["context_digest"],
               **({"repository_path": spec["repository_path"]} if spec.get("repository_path") else {}),
               "task_id": task_id, "review_id": row["review_id"]}
    COUNCIL._immutable_file(root / "review-outcome.json",
                            json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode())
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    for candidate in removable:
        candidate.unlink()
        directory = os.open(candidate.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def redact_verified(conn, ledger, root, receipt):
    """Keep task lineage, remove council-local evidence after a proven review effect."""
    root = Path(root)
    COUNCIL._private_dir(root)
    if not isinstance(receipt, dict) or receipt.get("status") != "verified" \
            or COUNCIL._immutable_file(root / "review-outcome.json") != \
            json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode():
        raise ValueError("verified review receipt differs")
    with closing(_db(ledger)) as effect:
        row = effect.execute("SELECT artifact_digest,head_sha,phase,review_id FROM effects WHERE operation_id=?",
                             (receipt["operation_id"],)).fetchone()
        if not row or tuple(row) != (receipt["artifact_digest"], receipt["head_sha"],
                                  "verified", receipt["review_id"]):
            raise ValueError("review effect is not verified for redaction")
    prefix = "pr-review-council-" + receipt["artifact_digest"][:32] + ":"
    rows = conn.execute("SELECT id,idempotency_key,assignee,status,body FROM tasks "
                        "WHERE idempotency_key LIKE ?", (prefix + "%",)).fetchall()
    profiles = {"generalist":"pr-review-generalist-v2", "reliability":"pr-review-reliability-v2",
                "mvp":"pr-review-mvp-v2", "security":"pr-review-security-v2",
                "synthesis":"pr-review-synthesis-v2"}
    found = {}
    for task in rows:
        body = json.loads(task["body"])
        role = body.get("role")
        if (role not in profiles or role in found or task["assignee"] != profiles[role]
                or task["idempotency_key"] != prefix + role or task["status"] not in ("done", "archived")
                or body.get("operation_id") != receipt["operation_id"]
                or body.get("artifact_digest") != receipt["artifact_digest"]):
            raise ValueError("council task lineage differs before redaction")
        runs = conn.execute("SELECT profile,outcome,ended_at FROM task_runs WHERE task_id=?", (task["id"],)).fetchall()
        if len(runs) != 1 or runs[0]["profile"] != profiles[role] \
                or runs[0]["outcome"] != "completed" or runs[0]["ended_at"] is None:
            raise ValueError("council task run differs before redaction")
        if conn.execute("SELECT 1 FROM task_attachments WHERE task_id=?", (task["id"],)).fetchone():
            raise ValueError("council task has unreviewed attachments")
        found[role] = task["id"]
    if set(found) not in ({"generalist", "reliability", "mvp", "synthesis"},
                         {"generalist", "reliability", "mvp", "security", "synthesis"}) \
            or found["synthesis"] != receipt["task_id"]:
        raise ValueError("council graph differs before redaction")
    with conn:
        for task_id in found.values():
            conn.execute("UPDATE task_runs SET summary=NULL,metadata=NULL,error=NULL WHERE task_id=?", (task_id,))
            conn.execute("UPDATE task_comments SET body='[redacted]' WHERE task_id=?", (task_id,))
            conn.execute("UPDATE task_events SET payload=NULL WHERE task_id=?", (task_id,))
            conn.execute("UPDATE tasks SET result=NULL,last_failure_error=NULL WHERE id=?", (task_id,))
    cleanup = {"operation_id": receipt["operation_id"], "artifact_digest": receipt["artifact_digest"],
               "review_id": receipt["review_id"], "task_ids": sorted(found.values())}
    COUNCIL._immutable_file(root / "review-cleanup.json",
                            json.dumps(cleanup, sort_keys=True, separators=(",", ":")).encode())
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def render(synthesis):
    lines = [f"Council verdict: {synthesis['verdict'].upper()}"]
    for finding in synthesis["findings"]:
        lines.extend((f"{finding['path']}:{finding['line']} [{finding['severity']}] "
                      f"{'required' if finding['required'] else 'advisory'}: {finding['claim']}",
                      f"Evidence: {finding['evidence']}", f"Suggestion: {finding['suggestion']}"))
    if not synthesis["findings"]:
        lines.append("No required changes identified." if synthesis["verdict"] == "approve"
                     else "No changed-line findings supplied; more information is required.")
    return "\n\n".join(lines)


def settle(kb, conn, tasks, spec, ledger, github, request, workspace):
    outputs = COUNCIL.handoffs(kb, conn, spec, tasks)
    if outputs is None:
        return None
    COUNCIL._verified_snapshot(workspace, spec)
    synthesis = outputs["synthesis"]
    event = {"approve": "APPROVE", "request-changes": "REQUEST_CHANGES",
             "needs-info": "COMMENT"}[synthesis["verdict"]]
    result = publish(ledger, github, request, event, render(synthesis), spec,
                     {role: outputs[role] for role in spec["specialists"]}, synthesis)
    purge_verified(ledger, workspace, spec, tasks["synthesis"])
    return result


def publish(path, github, request, event, body, spec, outputs, synthesis):
    verdict = COUNCIL.check_verdict(spec, outputs, synthesis)
    if body != render(synthesis):
        raise ValueError("GitHub body omits or changes synthesis evidence")
    expected_event = {"approve": "APPROVE", "request-changes": "REQUEST_CHANGES", "needs-info": "COMMENT"}[verdict]
    if (not isinstance(request, dict) or event != expected_event or request.get("operation_id") != spec["operation_id"]
            or request.get("artifact_digest") != spec["artifact_digest"]
            or request.get("head_sha") != spec["head_sha"]
            or request.get("base_sha") != spec["base_sha"]):
        raise ValueError("review publication is not bound to complete synthesis")
    if (not isinstance(request, dict) or set(request) !=
            {"operation_id", "repo", "number", "head_sha", "base_sha", "artifact_digest"}
            or event not in EVENTS or not isinstance(body, str) or not body.strip() or len(body) > 16000
            or not isinstance(request["base_sha"], str) or not SHA.fullmatch(request["base_sha"])
            or not isinstance(request["artifact_digest"], str) or not DIGEST.fullmatch(request["artifact_digest"])):
        raise ValueError("invalid council publication request")
    try:
        expected = identity("pr-review", request["repo"], request["number"], request["head_sha"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid review head identity") from error
    if request["operation_id"] != expected["operation_id"]:
        raise ValueError("review operation identity changed")
    marker = f'<!-- ai-pr-automation head={request["head_sha"]} -->'
    if '<!-- ai-pr-automation head=' in body:
        raise ValueError("review body already contains an automation marker")
    actor, author = github.actor(), github.author(request["repo"], request["number"])
    if not all(isinstance(login, str) and login for login in (actor, author)):
        raise ValueError("GitHub actor or PR author unavailable")
    if actor.casefold() == author.casefold() and event != "COMMENT":
        body = body.rstrip() + f"\n\nRecommendation: {event}"
        event = "COMMENT"
    content = body.rstrip() + "\n\n" + marker
    digest = hashlib.sha256(content.encode()).hexdigest()
    expected_state = {"state": "open", "head_sha": request["head_sha"], "base_sha": request["base_sha"]}

    def matches():
        if any(marker in (item.get("body") or "") for item in github.comments(request["repo"], request["number"])):
            raise ValueError("GitHub comment already carries the review marker")
        marked = [item for item in github.reviews_for_head(request["repo"], request["number"])
                  if marker in (item.get("body") or "")]
        if any(item.get("commit_id") != request["head_sha"] for item in marked):
            raise ValueError("GitHub marker is attached to a different commit")
        return marked

    with closing(_db(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM effects WHERE operation_id=?", (request["operation_id"],)).fetchone()
        binding = (request["repo"], request["number"], request["head_sha"], request["base_sha"],
                   request["artifact_digest"], digest, event)
        if row and tuple(row[key] for key in
                         ("repo", "pr", "head_sha", "base_sha", "artifact_digest", "body_digest", "event")) != binding:
            raise ValueError("review effect differs from prior attempt")
        existing = matches()
        if len(existing) > 1 or existing and (existing[0].get("author") != actor
                or existing[0].get("state") != event or existing[0].get("body") != content):
            raise ValueError("GitHub reviews conflict with exact-head marker")
        if existing:
            review_id = existing[0].get("id")
            if type(review_id) is not int or review_id <= 0:
                raise ValueError("GitHub review receipt is invalid")
            if not row:
                conn.execute("INSERT INTO effects VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (request["operation_id"], *binding, "verified", review_id))
            else:
                conn.execute("UPDATE effects SET phase='verified',review_id=? WHERE operation_id=?",
                             (review_id, request["operation_id"]))
            conn.commit()
            return {"status": "verified" if github.state(request["repo"], request["number"]) == expected_state else "superseded",
                    "review_id": review_id, "event": event, "head_sha": request["head_sha"]}
        state = github.state(request["repo"], request["number"])
        if state != expected_state:
            raise ValueError("PR head/base/state differs from reviewed snapshot")
        if row:
            raise ValueError("review post outcome uncertain; never retry automatically")
        conn.execute("INSERT INTO effects(operation_id,repo,pr,head_sha,base_sha,artifact_digest,body_digest,event,phase) "
                     "VALUES (?,?,?,?,?,?,?,?,'post_started')",
                     (request["operation_id"], *binding))
        conn.commit()
    if github.state(request["repo"], request["number"]) != state:
        raise ValueError("PR changed before GitHub review submission")
    response = github.post(request["repo"], request["number"], request["head_sha"], event, content)
    if (response.get("commit_id") != request["head_sha"] or response.get("state") != event
            or response.get("body") != content or response.get("author") != actor
            or type(response.get("id")) is not int or response["id"] <= 0):
        raise ValueError("posted review receipt differs from requested head")
    proof = matches()
    if len(proof) != 1 or any(proof[0].get(key) != response[key]
                              for key in ("id", "author", "commit_id", "state", "body")):
        raise ValueError("posted review could not be verified independently")
    after = github.state(request["repo"], request["number"])
    with closing(_db(path)) as conn:
        with conn:
            conn.execute("UPDATE effects SET phase='verified',review_id=? WHERE operation_id=? AND phase='post_started'",
                         (response["id"], request["operation_id"]))
    return {"status": "verified" if after == state else "superseded", "review_id": response["id"],
            "event": event, "head_sha": request["head_sha"]}
