#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "hermes-doc-m2a-gate-v1"
SECRET_MARKERS = ("KEY", "TOKEN", "PASSWORD", "SECRET")
COMPONENTS = [
    "Dockerfile.doc-writer",
    "agent-config/hermes/doc-config.yaml",
    "bin/doc-writer",
    "bin/doc-writer-publication",
    "bin/doc-writer-reconcile",
    "bin/doc-writer-server",
    "bin/hermes-doc-model",
    "bin/hermes-doc-request",
    "bin/hermes-run",
    "docker/Dockerfile.hermes-doc-egress",
    "docker/hermes-doc-egress.conf",
    "docker/hermes-oauth-openssl.cnf",
    "docker/initdb/01-schema.sql",
    "docker/initdb/02-agent-server.sql",
    "docker/initdb/03-human-review-queue.sql",
    "docker/initdb/04-pending-decision-approval.sql",
    "docker/initdb/06-hermes-doc-foundation.sql",
    "docker-compose.yml",
    "lib/queue.sh",
    "scripts/hermes-doc-gate.py",
]
CHECKS = [
    ("model-seam", ["bash", "tests/test-hermes-doc-model.sh"]),
    ("request-renderer", ["python3", "tests/test-hermes-doc-request.py"]),
    ("runs-adapter", ["python3", "tests/test-hermes-run.py"]),
    ("schema", ["bash", "tests/test-hermes-doc-schema.sh"]),
    ("publication", ["bash", "tests/test-doc-writer-publication.sh"]),
    ("doc-controller", ["bash", "tests/test-doc-writer-server.sh"]),
    ("legacy-doc", ["bash", "tests/test-doc-writer.sh"]),
    ("compose", ["bash", "tests/test-hermes-compose-contract.sh"]),
    ("egress", ["bash", "tests/test-hermes-doc-egress.sh"]),
    ("quarantine", ["bash", "tests/test-hermes-doc-quarantine.sh"]),
    ("runtime", ["bash", "tests/test-hermes-doc-spikes.sh"]),
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def framed_digest(paths):
    value = hashlib.sha256()
    for path in sorted(paths):
        value.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return value.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def component_paths():
    paths = [ROOT / value for value in COMPONENTS]
    paths += [ROOT / command[-1] for _, command in CHECKS]
    paths += [ROOT / "tests/fake-hermes-provider.py"]
    paths += sorted((ROOT / "agent-config/doc-writer/agents").glob("*.md"))
    paths += sorted((ROOT / "agent-config/doc-writer/handbook").glob("*.md"))
    return sorted(set(paths))


def topology():
    result = subprocess.run(
        ["docker", "compose", "--profile", "hermes-m2a", "config", "--format", "json"],
        cwd=ROOT, text=True, capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "docker compose config failed")
    value = json.loads(result.stdout)
    services = value["services"]
    hermes = services["hermes-doc"]
    if hermes.get("ports") or hermes.get("environment", {}).get("OPENAI_API_KEY"):
        raise RuntimeError("M2a must have no published Hermes port or provider key")
    if any("hermes-doc" in service.get("depends_on", {}) for name, service in services.items()
           if name != "hermes-doc"):
        raise RuntimeError("M2a must not route another service through Hermes")
    for service in services.values():
        environment = service.get("environment", {})
        for key in list(environment):
            if any(marker in key.upper() for marker in SECRET_MARKERS):
                environment[key] = "<redacted>" if environment[key] else ""
    selected = {
        "services": {name: services[name] for name in ("hermes-doc-preflight", "hermes-doc-egress", "hermes-doc")},
        "networks": {name: value.get("networks", {}).get(name, {}) for name in ("default", "hermes-doc")},
        "volumes": {"hermes_doc_state": value.get("volumes", {}).get("hermes_doc_state", {})},
    }
    encoded = canonical(selected).decode().replace(str(ROOT), "<repo>")
    return json.loads(encoded)


def snapshot():
    components = [{"path": str(path.relative_to(ROOT)), "sha256": digest(path.read_bytes())}
                  for path in component_paths()]
    rendered = topology()
    topology_sha = digest(canonical(rendered))
    generation = digest(canonical({"version": VERSION, "components": components,
                                   "topology_sha256": topology_sha}))
    return generation, components, topology_sha


def expected_commands():
    return {name: command for name, command in CHECKS}


def describe():
    generation, components, topology_sha = snapshot()
    print(json.dumps({"runtime_generation": generation, "components": components,
                      "topology_sha256": topology_sha, "checks": expected_commands()},
                     sort_keys=True, indent=2))


def verify(report_path):
    report_path = report_path.resolve(strict=True)
    if stat.S_IMODE(report_path.stat().st_mode) & 0o077:
        raise ValueError("gate report is not owner-only")
    report = json.loads(report_path.read_text())
    generation, components, topology_sha = snapshot()
    if (report.get("profile") != "m2a-foundation" or report.get("runtime_generation") != generation
            or report.get("components") != components or report.get("topology_sha256") != topology_sha
            or report.get("routing_authorized") is not False
            or report.get("paid_calls_authorized") is not False):
        raise ValueError("stale or wrong-generation gate report")
    checks = report.get("checks")
    if not isinstance(checks, list) or {item.get("name") for item in checks} != set(expected_commands()):
        raise ValueError("gate report check set mismatch")
    evidence_root = report_path.parent.resolve()
    for item in checks:
        if item.get("command") != expected_commands()[item["name"]] or item.get("exit_status") != 0:
            raise ValueError(f"failed or forged check: {item['name']}")
        artifact = (evidence_root / item.get("artifact", "")).resolve(strict=True)
        if evidence_root not in artifact.parents or digest(artifact.read_bytes()) != item.get("artifact_sha256"):
            raise ValueError(f"artifact mismatch: {item['name']}")
    runtime_path = (evidence_root / report.get("runtime_evidence", "")).resolve(strict=True)
    if evidence_root not in runtime_path.parents:
        raise ValueError("runtime evidence escapes report directory")
    if digest(runtime_path.read_bytes()) != report.get("runtime_evidence_sha256"):
        raise ValueError("runtime evidence artifact mismatch")
    runtime = json.loads(runtime_path.read_text())
    raw = (runtime_path.parent / "runtime-raw").resolve(strict=True)
    if evidence_root not in raw.parents:
        raise ValueError("raw runtime evidence escapes report directory")
    raw_files = {path.name: digest(path.read_bytes()) for path in raw.iterdir()
                 if path.is_file() and not path.is_symlink()}
    if runtime.get("raw_artifacts") != raw_files:
        raise ValueError("raw runtime artifact mismatch")
    required = ("container_id", "configured_image", "image_id", "init_pid", "started_at", "gateway_pid",
                "pid_mode", "cgroupns_mode", "mounts", "networks", "process_tree_sha256",
                "effective_config_sha256", "state_inventory_sha256", "hermes_run_id",
                "provider_capture_sha256", "trace_sha256", "trace_started_at", "trace_finished_at",
                "proxy_configured_image", "proxy_image_id")
    if runtime.get("runtime_generation") != generation or runtime.get("dropped_trace_events") != 0:
        raise ValueError("runtime evidence binding failed")
    if any(key not in runtime for key in required):
        raise ValueError("runtime evidence field missing")
    hashes = ("process_tree_sha256", "effective_config_sha256", "state_inventory_sha256",
              "provider_capture_sha256", "trace_sha256")
    component_map = {item["path"]: item["sha256"] for item in components}
    expected_image = topology()["services"]["hermes-doc"]["image"]
    container = json.loads((raw / "traced-container.json").read_text())[0]
    proxy = json.loads((raw / "proxy-container.json").read_text())[0]
    provider = json.loads((raw / "tls-request.json").read_text())
    run_status = json.loads((raw / "run-status.json").read_text())
    trace_metadata = json.loads((raw / "trace-metadata.json").read_text())
    traces = list(raw.glob("trace.[0-9]*"))
    derived_mounts = sorted([{"destination": item["Destination"], "type": item["Type"], "rw": item["RW"]}
                             for item in container["Mounts"]], key=lambda item: item["destination"])
    derived_networks = sorted(container["NetworkSettings"]["Networks"])
    destinations = {item.get("destination") for item in runtime["mounts"]}
    trace_errors = len(re.findall(r"attach:|Operation not permitted|ptrace\(",
                                  (raw / "trace-log.txt").read_text()))
    if (runtime["configured_image"] != expected_image
            or runtime["image_id"] != "sha256:" + expected_image.rsplit("@sha256:", 1)[1]
            or runtime["proxy_configured_image"] != "agent-fleet/hermes-doc-egress:m2a"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", runtime["proxy_image_id"])
            or any(not re.fullmatch(r"[0-9a-f]{64}", runtime[key]) for key in hashes)
            or runtime["container_id"] != container["Id"]
            or runtime["image_id"] != container["Image"]
            or runtime["init_pid"] != container["State"]["Pid"]
            or runtime["started_at"] != container["State"]["StartedAt"]
            or runtime["pid_mode"] != container["HostConfig"]["PidMode"]
            or runtime["cgroupns_mode"] != container["HostConfig"].get("CgroupnsMode", "")
            or runtime["mounts"] != derived_mounts or runtime["networks"] != derived_networks
            or runtime["proxy_configured_image"] != proxy["Config"]["Image"]
            or runtime["proxy_image_id"] != proxy["Image"]
            or runtime["process_tree_sha256"] != framed_digest([raw / "traced-processes.txt"])
            or runtime["effective_config_sha256"] != digest((raw / "config.yaml").read_bytes())
            or runtime["state_inventory_sha256"] != framed_digest([raw / "state-inventory.txt"])
            or runtime["provider_capture_sha256"] != framed_digest([raw / "tls-request.json"])
            or runtime["trace_sha256"] != framed_digest(traces) or not traces
            or runtime["effective_config_sha256"] != component_map["agent-config/hermes/doc-config.yaml"]
            or run_status.get("run_id") != runtime["hermes_run_id"]
            or provider.get("path") != "/v1/responses" or provider.get("body", {}).get("tools")
            or trace_metadata.get("runtime_generation") != generation
            or trace_metadata.get("container_id") != runtime["container_id"]
            or trace_metadata.get("hermes_run_id") != runtime["hermes_run_id"]
            or trace_metadata.get("trace_started_at") != runtime["trace_started_at"]
            or trace_metadata.get("trace_finished_at") != runtime["trace_finished_at"]
            or trace_metadata.get("dropped_trace_events") != trace_errors
            or not {"/opt/data", "/opt/data/config.yaml"}.issubset(destinations)
            or len(runtime["networks"]) != 1
            or not isinstance(runtime["init_pid"], int) or runtime["init_pid"] < 1
            or not isinstance(runtime["gateway_pid"], int) or runtime["gateway_pid"] < 1
            or runtime["trace_started_at"] >= runtime["trace_finished_at"]):
        raise ValueError("runtime evidence semantics failed")
    print(generation)


def run(output):
    generation, components, topology_sha = snapshot()
    output = output or Path.home() / ".local/state/ai-pr-automation/hermes-doc/gates" / f"m2-{generation}.json"
    output = output.resolve()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    evidence = output.parent / f"m2-{generation}.evidence"
    evidence.mkdir(mode=0o700, exist_ok=True)
    records = []
    failed = False
    for name, command in CHECKS:
        artifact = evidence / f"{name}.log"
        started = now()
        env = os.environ.copy()
        if name == "runtime":
            env["HERMES_EVIDENCE_FILE"] = str(evidence / "runtime.json")
            env["HERMES_RUNTIME_GENERATION"] = generation
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, env=env)
        artifact.write_text(result.stdout + result.stderr)
        records.append({"name": name, "command": command, "exit_status": result.returncode,
                        "started_at": started, "finished_at": now(),
                        "artifact": str(artifact.relative_to(output.parent)),
                        "artifact_sha256": digest(artifact.read_bytes())})
        failed |= result.returncode != 0
        if failed:
            break
    runtime_artifact = evidence / "runtime.json"
    report = {"schema_version": 1, "profile": "m2a-foundation",
              "runtime_generation": generation, "created_at": now(),
              "routing_authorized": False, "paid_calls_authorized": False,
              "components": components, "topology_sha256": topology_sha,
              "checks": records,
              "runtime_evidence": str(runtime_artifact.relative_to(output.parent)),
              "runtime_evidence_sha256": digest(runtime_artifact.read_bytes()) if runtime_artifact.exists() else ""}
    output.write_bytes(canonical(report) + b"\n")
    os.chmod(output, 0o600)
    if failed:
        raise SystemExit(1)
    verify(output)


def main():
    parser = argparse.ArgumentParser(description="Generate or verify Hermes document M2a gate evidence")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("describe")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--output", type=Path)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.action == "describe":
        describe()
    elif args.action == "run":
        run(args.output)
    else:
        verify(args.report)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"hermes-doc-gate: {error}", file=sys.stderr)
        raise SystemExit(2)
