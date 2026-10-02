"""Submit PR work to installed Hermes profiles without the fleet queue."""
import json
import urllib.error
import urllib.parse
import urllib.request

from hermes_direct_pr_journal import identity

PROFILES = {"pr-review": "pr-review-v1", "pr-maintain": "pr-maintain-v1"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
INSTRUCTIONS = {
    "pr-review": "Review this exact PR head and post one head-bound review.",
    "pr-maintain": "Handle actionable feedback at this exact PR head in one fix pass.",
}


def _request(base_url, kind, key, method, run_id="", body=None, operation_id=""):
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"}
            or not parsed.port or (parsed.hostname == "host.docker.internal" and parsed.port != 8642)
            or parsed.path not in {"", "/"} or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Hermes API must be the local host gateway")
    path = f"/p/{PROFILES[kind]}/v1/runs"
    if run_id:
        if not isinstance(run_id, str):
            raise ValueError("invalid Hermes run ID")
        path += "/" + urllib.parse.quote(run_id, safe="")
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if raw is not None:
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = operation_id
    request = urllib.request.Request(base_url.rstrip("/") + path, data=raw, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=10) as response:
            status_code, result = response.status, response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        status_code = error.code
        error.close()
        raise ValueError(f"Hermes API returned HTTP {status_code}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise ValueError("Hermes API unavailable") from error
    if len(result) > 1024 * 1024:
        raise ValueError("Hermes API response too large")
    try:
        value = json.loads(result)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid Hermes API response") from error
    if not isinstance(value, dict):
        raise ValueError("invalid Hermes API response")
    return status_code, value


def submit(kind, repo, number, head_sha, key, *, feedback_digest=None,
           base_url="http://127.0.0.1:8642"):
    if kind not in PROFILES:
        raise ValueError("unsupported PR kind")
    resolved = identity(kind, repo, number, head_sha, feedback_digest)
    operation = resolved["operation_id"]
    target = {"operation_id": operation, "repo": resolved["repo"], "number": number, "head_sha": head_sha}
    if kind == "pr-maintain":
        target["feedback_digest"] = resolved["feedback_digest"]
    body = {"input": json.dumps(target, sort_keys=True, separators=(",", ":")),
            "session_id": operation,
            "instructions": (INSTRUCTIONS[kind] + " Return only JSON with detail, nonce, posted_ref, status. "
                             f"Use nonce {operation[-32:]}.")}
    code, response = _request(base_url, kind, key, "POST", body=body, operation_id=operation)
    if (code != 202 or not isinstance(response.get("run_id"), str) or not response["run_id"]
            or not isinstance(response.get("status"), str) or not response["status"]
            or type(response.get("replayed")) is not bool):
        raise ValueError("invalid Hermes run admission")
    return {"operation_id": operation, "run_id": response["run_id"],
            "status": response["status"], "replayed": response["replayed"]}


def status(kind, run_id, key, *, base_url="http://127.0.0.1:8642"):
    if kind not in PROFILES:
        raise ValueError("unsupported PR kind")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("invalid Hermes run ID")
    code, response = _request(base_url, kind, key, "GET", run_id=run_id)
    if code != 200 or response.get("run_id") != run_id or not isinstance(response.get("status"), str):
        raise ValueError("invalid Hermes run status")
    return {"run_id": run_id, "status": response["status"]}
