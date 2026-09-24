#!/usr/bin/env python3
"""Pure PR-safety result mapping, validation, and immutable handoff publication."""
import hashlib
import json
import os
import pathlib
import re
import sqlite3
from contextlib import closing

ROLE_SET = {"review", "security", "reliability", "architecture"}
MAX_ITEMS = 50
MAX_TEXT = 4_000
MAX_QUOTE = 2_000
SESSION_MATCH_SKEW_SECONDS = 60
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
OPERATION_RE = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def strict_object(value, keys):
    return isinstance(value, dict) and set(value) == set(keys)


def normalize_safety(value):
    if not isinstance(value, dict):
        return value
    value = dict(value)
    value.pop("snapshot_path", None)
    value.pop("policy_path", None)
    incident = value.get("incident")
    if isinstance(incident, dict) and incident.get("candidate") is True:
        value["status"] = "incident_candidate"
    return value


def valid_council_incident_evidence(value):
    return isinstance(value, list) and bool(value) and all(
        strict_object(item, {"path", "line", "side", "quote"})
        and isinstance(item["path"], str) and bool(item["path"]) and len(item["path"]) <= 4096
        and not pathlib.PurePosixPath(item["path"]).is_absolute()
        and ".." not in pathlib.PurePosixPath(item["path"]).parts
        and type(item["line"]) is int and item["line"] > 0 and item["side"] in {"old", "new"}
        and isinstance(item["quote"], str) and bool(item["quote"]) and len(item["quote"]) <= 2000
        for item in value
    )


def map_council_safety(package, payload, nonce):
    copied = json.loads(canonical(package))
    incident = copied["incident"]
    predicates = ("changed_line_cause", "concrete_trigger", "severe_impact",
                  "high_confidence_chain", "stop_rollback_or_page")
    verdict_candidate = copied["verdict"] == "incident_candidate"
    if incident["candidate"] is not verdict_candidate \
            or incident["candidate"] is not all(incident[key] is True for key in predicates):
        raise ValueError("inconsistent council incident candidate")
    if (verdict_candidate or incident["candidate"] or any(incident[key] is True for key in predicates)) \
            and not valid_council_incident_evidence(incident.get("evidence")):
        raise ValueError("council incident candidate requires evidence")
    incident_candidate = verdict_candidate and all(incident[key] is True for key in predicates)
    human = list(copied["human_decisions_needed"])
    for item in copied["dissent"]:
        if item["disposition"] == "unresolved" and item not in human:
            human.append(item)
    for item in copied["residual_risk"]:
        if item["requires_human_decision"] is True and item not in human:
            human.append(item)
    status = {"clear": "clear", "changes_requested": "changes_requested",
              "needs_human_decision": "needs_human_decision", "incident_candidate": "needs_human_decision",
              "inconclusive": "needs_human_decision"}[copied["verdict"]]
    if incident_candidate:
        status = "incident_candidate"
    gaps = bool(copied["findings"] or human or incident["evidence"]
                or copied["coverage"].get("gaps") or copied["documentation"].get("required_updates")
                or copied["observability"].get("recommended_metrics")
                or copied["observability"].get("recommended_slos_or_runbooks"))
    if status == "clear" and gaps:
        status = "changes_requested"
    coverage = copied["coverage"]
    coverage["council"] = {"workflow_id": copied["workflow_id"],
                           "artifact_digest": copied["artifact_digest"],
                           "dissent": copied["dissent"], "residual_risk": copied["residual_risk"]}
    mapped_incident = dict(incident, candidate=incident_candidate)
    identity = {key: payload[key] for key in ("operation_id", "repo", "pr", "head_sha", "base_sha",
                                               "diff_hash", "policy_version", "policy_digest")}
    return {"nonce": nonce, **identity, "status": status, "intent": copied["intent"],
            "findings": copied["findings"], "coverage": coverage, "documentation": copied["documentation"],
            "observability": copied["observability"], "incident": mapped_incident,
            "human_decisions_needed": human}


def valid_safety(value, payload, nonce):
    required = {"nonce", "operation_id", "repo", "pr", "head_sha", "base_sha", "diff_hash",
                "policy_version", "policy_digest", "status", "intent", "findings", "coverage",
                "documentation", "observability", "incident", "human_decisions_needed"}
    if not strict_object(value, required) or value.get("nonce") != nonce:
        return False
    for key in ("operation_id", "repo", "pr", "head_sha", "base_sha", "diff_hash",
                "policy_version", "policy_digest"):
        if value.get(key) != payload.get(key):
            return False
    if value["status"] not in {"clear", "changes_requested", "needs_human_decision",
                               "incident_candidate", "superseded"}:
        return False
    if not isinstance(value["findings"], list) or not isinstance(value["human_decisions_needed"], list):
        return False
    incident = value.get("incident")
    if not isinstance(incident, dict) or not isinstance(incident.get("candidate"), bool):
        return False
    if (value["status"] == "incident_candidate") != incident["candidate"]:
        return False
    if value["status"] == "clear" and (value["findings"] or value["human_decisions_needed"]
                                         or incident["candidate"]):
        return False
    return True


def bounded_string(value, limit=MAX_TEXT):
    return isinstance(value, str) and bool(value) and len(value) <= limit


def bounded_list(value):
    return isinstance(value, list) and len(value) <= MAX_ITEMS


def valid_evidence(item, lines):
    if not isinstance(item, dict) or set(item) != {"path", "line", "side", "quote"} \
            or not bounded_string(item["path"], limit=4096) \
            or pathlib.PurePosixPath(item["path"]).is_absolute() \
            or ".." in pathlib.PurePosixPath(item["path"]).parts \
            or type(item["line"]) is not int or item["line"] <= 0 or item["side"] not in {"new", "old"} \
            or not bounded_string(item["quote"], limit=MAX_QUOTE):
        return False
    return any(path == item["path"] and line == item["line"] and side == item["side"]
               and item["quote"] in text for path, line, side, text in lines)


def valid_evidence_list(value, lines):
    return bounded_list(value) and all(valid_evidence(item, lines) for item in value)


def valid_dissent(item, lines):
    return isinstance(item, dict) and set(item) == {
        "source_role", "claim", "evidence", "disposition", "rationale"
    } and item["source_role"] in ROLE_SET and bounded_string(item["claim"]) \
        and valid_evidence_list(item["evidence"], lines) \
        and item["disposition"] in {"accepted", "rejected", "unresolved"} \
        and bounded_string(item["rationale"])


def valid_residual(item, lines):
    return isinstance(item, dict) and set(item) == {
        "source_role", "claim", "evidence", "requires_human_decision"
    } and item["source_role"] in ROLE_SET and bounded_string(item["claim"]) \
        and valid_evidence_list(item["evidence"], lines) \
        and type(item["requires_human_decision"]) is bool


def valid_finding(item, lines):
    return isinstance(item, dict) and set(item) == {
        "role", "claim", "evidence", "confidence", "dissent", "residual_risk"
    } and item["role"] in ROLE_SET and bounded_string(item["claim"]) \
        and valid_evidence_list(item["evidence"], lines) \
        and item["confidence"] in {"low", "medium", "high"} \
        and bounded_list(item["dissent"]) and all(valid_dissent(value, lines) for value in item["dissent"]) \
        and bounded_list(item["residual_risk"]) \
        and all(valid_residual(value, lines) for value in item["residual_risk"])


def valid_council_metadata(metadata, role, workflow_id, artifact_digest, lines):
    if not isinstance(metadata, dict) or len(canonical(metadata).encode()) > 65_536 \
            or metadata.get("workflow_id") != workflow_id \
            or metadata.get("artifact_digest") != artifact_digest:
        return False
    if role != "synthesis":
        if set(metadata) != {"workflow_id", "artifact_digest", "role", "verdict", "claims", "evidence",
                             "confidence", "dissent", "residual_risk"} \
                or metadata.get("role") != role \
                or metadata.get("verdict") not in {"clear", "findings", "needs_human_decision", "inconclusive"} \
                or metadata.get("confidence") not in {"low", "medium", "high"} \
                or not bounded_list(metadata.get("claims")) \
                or not all(bounded_string(value) for value in metadata["claims"]) \
                or not valid_evidence_list(metadata.get("evidence"), lines) \
                or not bounded_list(metadata.get("dissent")) \
                or not all(valid_dissent(value, lines) for value in metadata["dissent"]) \
                or not bounded_list(metadata.get("residual_risk")) \
                or not all(valid_residual(value, lines) for value in metadata["residual_risk"]):
            return False
        substantive = any((metadata["claims"], metadata["evidence"], metadata["dissent"],
                           metadata["residual_risk"]))
        return not substantive if metadata["verdict"] == "clear" else substantive
    expected = {"workflow_id", "artifact_digest", "verdict", "intent", "findings", "coverage",
                "documentation", "observability", "incident", "human_decisions_needed", "dissent",
                "residual_risk"}
    incident = metadata.get("incident")
    incident_keys = {"candidate", "changed_line_cause", "concrete_trigger", "severe_impact",
                     "high_confidence_chain", "stop_rollback_or_page", "evidence"}
    verdict = metadata.get("verdict")
    incident_valid = isinstance(incident, dict) and set(incident) == incident_keys \
        and all(type(incident[key]) is bool for key in incident_keys - {"evidence"}) \
        and valid_evidence_list(incident["evidence"], lines)
    predicates = incident_keys - {"candidate", "evidence"}
    incident_signal = incident_valid and (verdict == "incident_candidate"
        or any(incident[key] for key in incident_keys - {"evidence"}))
    return set(metadata) == expected \
        and verdict in {"clear", "changes_requested", "needs_human_decision", "incident_candidate", "inconclusive"} \
        and all(isinstance(metadata.get(key), dict)
                for key in ("intent", "coverage", "documentation", "observability")) \
        and bounded_list(metadata.get("findings")) \
        and all(valid_finding(value, lines) for value in metadata["findings"]) \
        and incident_valid and incident["candidate"] == (verdict == "incident_candidate") \
        and incident["candidate"] == all(incident[key] for key in predicates) \
        and (not incident_signal or bool(incident["evidence"])) \
        and bounded_list(metadata.get("human_decisions_needed")) \
        and all(bounded_string(value) for value in metadata["human_decisions_needed"]) \
        and bounded_list(metadata.get("dissent")) \
        and all(valid_dissent(value, lines) for value in metadata["dissent"]) \
        and bounded_list(metadata.get("residual_risk")) \
        and all(valid_residual(value, lines) for value in metadata["residual_risk"])


def item_digest(item, fields):
    identity = {key: item[key] for key in fields}
    return hashlib.sha256(canonical(identity).encode()).hexdigest()


def union_ledgers(specialists, synthesis):
    synthesis_sources = [synthesis, *synthesis["findings"]]
    proposed = {item_digest(item, ("source_role", "claim", "evidence")): item
                for metadata in synthesis_sources for item in metadata["dissent"]}
    dissent = {}
    for metadata in [*specialists, *synthesis_sources]:
        for item in metadata["dissent"]:
            digest = item_digest(item, ("source_role", "claim", "evidence"))
            value = dict(item)
            if all(metadata is not source for source in synthesis_sources):
                proposal = proposed.get(digest)
                value["disposition"] = proposal["disposition"] if proposal else "unresolved"
                value["rationale"] = proposal["rationale"] if proposal else "No synthesis disposition supplied."
            dissent[(value["source_role"], digest)] = value
    residual = {}
    for metadata in [*specialists, *synthesis_sources]:
        for item in metadata["residual_risk"]:
            digest = item_digest(item, ("source_role", "claim", "evidence", "requires_human_decision"))
            residual[(item["source_role"], digest)] = item
    return ([dissent[key] for key in sorted(dissent)], [residual[key] for key in sorted(residual)])


def run_usage(home, profile, run, model):
    path = pathlib.Path(home) / "profiles" / profile / "state.db"
    started_at, ended_at = getattr(run, "started_at", None), getattr(run, "ended_at", None)
    if not path.is_file() or path.is_symlink() or type(started_at) is not int \
            or type(ended_at) is not int or ended_at < started_at:
        return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            required = {"source", "started_at", "ended_at", "input_tokens", "output_tokens"}
            if not required <= columns:
                return None
            token_columns = ["input_tokens", "output_tokens"]
            token_columns += [name for name in ("cache_read_tokens", "cache_write_tokens") if name in columns]
            where = "source = ? AND started_at BETWEEN ? AND ? AND ended_at IS NOT NULL " \
                    "AND ended_at BETWEEN started_at AND ?"
            values = ["kanban", started_at - SESSION_MATCH_SKEW_SECONDS,
                      ended_at + SESSION_MATCH_SKEW_SECONDS, ended_at + SESSION_MATCH_SKEW_SECONDS]
            if "model" in columns:
                where += " AND model = ?"
                values.append(model)
            rows = db.execute(
                f"SELECT {','.join(token_columns)} FROM sessions WHERE {where} LIMIT 2", values
            ).fetchall()
    except sqlite3.Error:
        return None
    if len(rows) != 1 or any(type(value) is not int or value < 0 for value in rows[0]):
        return None
    usage = dict(zip(token_columns, rows[0]))
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage


def render_safety_handoff(payload, value):
    body = ("<!-- pr-safety identity\n" + "\n".join(f"{key}: {payload[key]}" for key in
        ("operation_id", "repo", "pr", "head_sha", "base_sha", "diff_hash", "policy_version"))
        + f"\nstatus: {value['status']}\nincident_candidate: "
        + f"{str(value['incident']['candidate']).lower()}\n-->\n\n"
        + "## Concrete breakage\n\n```json\n" + canonical(value["findings"]) + "\n```\n\n"
        + "## Human decisions\n\n```json\n"
        + canonical({"intent": value["intent"], "items": value["human_decisions_needed"]}) + "\n```\n")
    coverage = value.get("coverage")
    council = coverage.get("council") if isinstance(coverage, dict) else None
    if isinstance(council, dict):
        body += "\n## Council context\n\n```json\n" + canonical(council) + "\n```\n"
    return body.encode()


def publish_safety_handoff(root, payload, value):
    repo, operation_id, pr = payload.get("repo"), payload.get("operation_id"), payload.get("pr")
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo) \
            or not isinstance(operation_id, str) or not OPERATION_RE.fullmatch(operation_id) \
            or type(pr) is not int or pr <= 0:
        raise ValueError("invalid safety handoff identity")
    root = pathlib.Path(root)
    if root.is_symlink():
        raise ValueError("unsafe safety handoff root")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    name = f"{repo.replace('/', '__')}__pr{pr}__{operation_id}.md"
    target = root / name
    if target.parent != root:
        raise ValueError("unsafe safety handoff path")
    data = render_safety_handoff(payload, value)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    except FileExistsError:
        if target.is_symlink() or target.read_bytes() != data:
            raise ValueError("existing safety handoff differs")
    else:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    return str(target), hashlib.sha256(data).hexdigest()
