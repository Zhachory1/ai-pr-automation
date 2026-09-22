#!/usr/bin/env python3
"""Pinned Hermes Runs API conformance client.

Default is auth-only and makes no model calls. --run-profile performs one no-effect paid run and
proves durable idempotent replay + conflict + terminal polling. Never prints bearer keys.
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


class Client:
    def __init__(self, base, timeout=10):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def request(self, method, path, key, body=None, idempotency_key=None):
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read(); status = response.status; response_headers = dict(response.headers)
        except urllib.error.HTTPError as error:
            raw = error.read(); status = error.code; response_headers = dict(error.headers)
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw.decode("utf-8", "replace")[:200]}
        return status, payload, response_headers


def load_keys(path):
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1 or not isinstance(data.get("profiles"), dict):
        raise SystemExit("invalid Hermes API key bundle")
    return data


def wait_ready(client, keys, seconds):
    pending = dict(keys["profiles"]); deadline = time.time() + seconds; last = {}
    while pending and time.time() < deadline:
        for profile, key in list(pending.items()):
            try:
                status, _, _ = client.request("GET", f"/p/{profile}/v1/models", key)
                if status == 200:
                    del pending[profile]
                else:
                    last[profile] = f"HTTP {status}"
            except (urllib.error.URLError, TimeoutError) as error:
                last[profile] = str(getattr(error, "reason", error))
        if pending:
            time.sleep(0.5)
    if pending:
        detail = ", ".join(f"{profile}: {last.get(profile, 'no response')}" for profile in pending)
        raise SystemExit(f"Hermes API profiles not ready within {seconds}s: {detail}")


def retry_get(client, path, key, seconds):
    deadline = time.time() + seconds; last = None
    while time.time() < deadline:
        try:
            return client.request("GET", path, key)
        except (urllib.error.URLError, TimeoutError) as error:
            last = str(getattr(error, "reason", error))
            time.sleep(0.25)
    raise SystemExit(f"Hermes API probe timed out after {seconds}s: {path}: {last}")


def auth_probe(client, keys, retry_seconds=15):
    profiles = keys["profiles"]
    for profile, key in profiles.items():
        status, _, _ = retry_get(client, f"/p/{profile}/v1/models", key, retry_seconds)
        if status != 200:
            raise SystemExit(f"correct key failed for {profile}: HTTP {status}")
        wrong_keys = [candidate for other, candidate in profiles.items() if other != profile]
        wrong_keys.append("definitely-wrong-profile-key-000000000000000000")
        for wrong in wrong_keys:
            status, _, _ = retry_get(client, f"/p/{profile}/v1/models", wrong, retry_seconds)
            if status != 401:
                raise SystemExit(f"cross-profile/wrong key did not fail closed for {profile}: HTTP {status}")
    print(f"auth conformance passed for {len(profiles)} profiles")


def run_probe(client, keys, profile, timeout):
    if profile not in keys["profiles"]:
        raise SystemExit(f"profile missing from key bundle: {profile}")
    key = keys["profiles"][profile]
    idem = f"conformance-{int(time.time())}-{os.getpid()}"
    body = {"input": "Return exactly CONFORMANCE_OK. Do not call tools.",
            "session_id": idem, "instructions": "No tools. Return exactly CONFORMANCE_OK."}
    status, first, _ = client.request("POST", f"/p/{profile}/v1/runs", key, body, idem)
    if (status != 202 or set(first) != {"run_id", "status", "replayed"}
            or not isinstance(first.get("run_id"), str) or first.get("status") != "started"
            or first.get("replayed") is not False):
        raise SystemExit(f"run admission/schema failed: HTTP {status}")
    status, replay, headers = client.request("POST", f"/p/{profile}/v1/runs", key, body, idem)
    if (status != 202 or set(replay) != {"run_id", "status", "replayed"}
            or replay.get("run_id") != first["run_id"] or replay.get("replayed") is not True
            or headers.get("Idempotency-Replayed") != "true"):
        raise SystemExit("identical replay did not return original run")
    changed = dict(body, input="DIFFERENT")
    status, _, _ = client.request("POST", f"/p/{profile}/v1/runs", key, changed, idem)
    if status != 409:
        raise SystemExit(f"changed-body key reuse did not conflict: HTTP {status}")
    deadline = time.time() + timeout
    terminal = None
    while time.time() < deadline:
        status, current, _ = client.request("GET", f"/p/{profile}/v1/runs/{first['run_id']}", key)
        if status != 200:
            raise SystemExit(f"run poll failed: HTTP {status}")
        if current.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
            terminal = current; break
        time.sleep(1)
    if terminal is None:
        raise SystemExit("run did not reach terminal state")
    required = {"object", "run_id", "status", "created_at", "updated_at", "last_event",
                "session_id", "model", "output", "usage"}
    usage = terminal.get("usage")
    if (not required.issubset(terminal) or terminal.get("object") != "hermes.run"
            or terminal.get("run_id") != first["run_id"] or terminal.get("status") != "completed"
            or terminal.get("output", "").strip() != "CONFORMANCE_OK" or not isinstance(usage, dict)
            or not {"input_tokens", "output_tokens", "total_tokens"}.issubset(usage)
            or not all(isinstance(usage[key], int) for key in ("input_tokens", "output_tokens", "total_tokens"))):
        raise SystemExit(f"unexpected terminal result/schema: {terminal.get('status')}")
    print(f"run conformance passed for {profile}; run_id={first['run_id']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8642")
    parser.add_argument("--keys-file", required=True)
    parser.add_argument("--run-profile", help="optional paid no-effect run probe")
    parser.add_argument("--run-timeout", type=int, default=180)
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--probe-retry-seconds", type=int, default=15)
    parser.add_argument("--request-timeout", type=int, default=5)
    args = parser.parse_args()
    keys = load_keys(args.keys_file); client = Client(args.base_url, args.request_timeout)
    if args.wait_seconds:
        wait_ready(client, keys, args.wait_seconds)
    auth_probe(client, keys, args.probe_retry_seconds)
    if args.run_profile:
        run_probe(client, keys, args.run_profile, args.run_timeout)


if __name__ == "__main__":
    main()
