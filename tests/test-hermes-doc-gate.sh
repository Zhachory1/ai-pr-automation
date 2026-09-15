#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
chmod 700 "$tmp"

gate=scripts/hermes-doc-gate.py
"$gate" describe > "$tmp/description.json"
python3 - "$tmp" <<'PY'
import hashlib, json, os, pathlib, shutil, sys
root = pathlib.Path(sys.argv[1])
description = json.loads((root / "description.json").read_text())
generation = description["runtime_generation"]
image_digest = "6d7285e1476d0661fc347e3d55245c99decb781d76d39d67582166d2c9561874"
image = "nousresearch/hermes-agent@sha256:" + image_digest
mounts = [{"Destination": "/opt/data", "Type": "volume", "RW": True},
          {"Destination": "/opt/data/config.yaml", "Type": "bind", "RW": False}]
raw = root / "runtime-raw"; raw.mkdir()
def put(name, value):
    (raw / name).write_text(json.dumps(value) if not isinstance(value, str) else value)
def framed(paths):
    value = hashlib.sha256()
    for path in sorted(paths):
        value.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return value.hexdigest()
put("traced-container.json", [{"Id": "container", "Image": "sha256:" + image_digest,
    "Config": {"Image": image}, "State": {"Pid": 1, "StartedAt": "2026-09-15T00:00:00Z"},
    "HostConfig": {"PidMode": "", "CgroupnsMode": "private"}, "Mounts": mounts,
    "NetworkSettings": {"Networks": {"internal": {}}}}])
put("proxy-container.json", [{"Image": "sha256:" + "e" * 64,
    "Config": {"Image": "agent-fleet/hermes-doc-egress:m2a"}}])
put("traced-processes.txt", "2 10000 hermes hermes gateway run\n")
shutil.copyfile("agent-config/hermes/doc-config.yaml", raw / "config.yaml")
put("state-inventory.txt", "unchanged\n")
put("tls-request.json", {"path": "/v1/responses", "body": {}})
put("run-status.json", {"run_id": "run-1", "status": "completed"})
put("trace-log.txt", "strace: Process 2 attached\n")
put("trace.2", 'connect(1) = 0\n')
metadata = {"runtime_generation": generation, "container_id": "container", "hermes_run_id": "run-1",
            "trace_started_at": "2026-09-15T00:00:01Z", "trace_finished_at": "2026-09-15T00:00:02Z",
            "dropped_trace_events": 0}
put("trace-metadata.json", metadata)
derived_mounts = sorted([{"destination": item["Destination"], "type": item["Type"], "rw": item["RW"]}
                         for item in mounts], key=lambda item: item["destination"])
runtime = {**metadata, "configured_image": image, "image_id": "sha256:" + image_digest,
    "init_pid": 1, "started_at": "2026-09-15T00:00:00Z", "gateway_pid": 2,
    "pid_mode": "", "cgroupns_mode": "private", "mounts": derived_mounts, "networks": ["internal"],
    "process_tree_sha256": framed([raw / "traced-processes.txt"]),
    "effective_config_sha256": hashlib.sha256((raw / "config.yaml").read_bytes()).hexdigest(),
    "state_inventory_sha256": framed([raw / "state-inventory.txt"]),
    "provider_capture_sha256": framed([raw / "tls-request.json"]),
    "trace_sha256": framed([raw / "trace.2"]), "proxy_configured_image": "agent-fleet/hermes-doc-egress:m2a",
    "proxy_image_id": "sha256:" + "e" * 64,
    "raw_artifacts": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in raw.iterdir()}}
(root / "runtime.json").write_text(json.dumps(runtime))
checks = []
for name, command in description["checks"].items():
    artifact = root / f"{name}.log"; artifact.write_text(f"PASS: {name}\n")
    checks.append({"name": name, "command": command, "exit_status": 0,
                   "started_at": "2026-09-15T00:00:00Z", "finished_at": "2026-09-15T00:00:01Z",
                   "artifact": artifact.name, "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})
report = {"schema_version": 1, "profile": "m2a-foundation", "runtime_generation": generation,
          "created_at": "2026-09-15T00:00:03Z", "routing_authorized": False,
          "paid_calls_authorized": False, "components": description["components"],
          "topology_sha256": description["topology_sha256"], "checks": checks,
          "runtime_evidence": "runtime.json",
          "runtime_evidence_sha256": hashlib.sha256((root / "runtime.json").read_bytes()).hexdigest()}
(root / "report.json").write_text(json.dumps(report)); os.chmod(root / "report.json", 0o600)
PY

"$gate" verify "$tmp/report.json" >/dev/null
cp "$tmp/report.json" "$tmp/wrong-generation.json"
python3 - "$tmp/wrong-generation.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1]); value = json.loads(path.read_text())
value["runtime_generation"] = "0" * 64; path.write_text(json.dumps(value))
PY
chmod 600 "$tmp/wrong-generation.json"
if "$gate" verify "$tmp/wrong-generation.json" >/dev/null 2>&1; then
  echo "FAIL: wrong generation accepted" >&2; exit 1
fi
printf 'forged\n' >> "$tmp/runtime.log"
if "$gate" verify "$tmp/report.json" >/dev/null 2>&1; then
  echo "FAIL: modified artifact accepted" >&2; exit 1
fi
# Restore command log, then prove retained raw evidence is bound.
printf 'PASS: runtime\n' > "$tmp/runtime.log"
printf 'forged\n' >> "$tmp/runtime-raw/trace.2"
if "$gate" verify "$tmp/report.json" >/dev/null 2>&1; then
  echo "FAIL: modified raw trace accepted" >&2; exit 1
fi
printf 'connect(1) = 0\n' > "$tmp/runtime-raw/trace.2"
# Prove dropped trace evidence fails closed.
python3 - "$tmp/runtime.json" "$tmp/report.json" <<'PY'
import hashlib, json, pathlib, sys
path = pathlib.Path(sys.argv[1]); value = json.loads(path.read_text())
value["dropped_trace_events"] = 1; path.write_text(json.dumps(value))
report = pathlib.Path(sys.argv[2]); body = json.loads(report.read_text())
body["runtime_evidence_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
report.write_text(json.dumps(body))
PY
chmod 600 "$tmp/report.json"
if "$gate" verify "$tmp/report.json" >/dev/null 2>&1; then
  echo "FAIL: dropped trace accepted" >&2; exit 1
fi

echo "PASS: M2a gate rejects stale, forged, and dropped-trace evidence"
