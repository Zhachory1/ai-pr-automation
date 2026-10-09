"""Bounded host-only GitHub adapter for pinned PR evidence and one review effect."""

import json
import subprocess


class GitHub:
    def _api(self, path, *, paginate=False, accept=None, post=None):
        command = ["gh", "api"]
        if paginate:
            command.append("--paginate")
        if accept:
            command.extend(["-H", f"Accept: {accept}"])
        if post is not None:
            command.extend(["-X", "POST", "--input", "-"])
        command.append(path)
        result = subprocess.run(command, input=None if post is None else json.dumps(post).encode(),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45, check=False)
        if result.returncode or not result.stdout or len(result.stdout) > 1_048_576:
            raise ValueError("GitHub API unavailable or response exceeds limit")
        if accept:
            return result.stdout
        text = result.stdout.decode("utf-8")
        decoder = json.JSONDecoder()
        if not paginate:
            return json.loads(text)
        items, offset = [], 0
        while offset < len(text):
            value, offset = decoder.raw_decode(text, offset)
            if not isinstance(value, list):
                raise ValueError("invalid GitHub pagination")
            items.extend(value)
            if len(items) > 1000:
                raise ValueError("GitHub pagination exceeds limit")
            while offset < len(text) and text[offset].isspace():
                offset += 1
        return items

    def pr(self, repo, number):
        value = self._api(f"repos/{repo}/pulls/{number}")
        return {"state": value["state"], "head_sha": value["head"]["sha"],
                "base_sha": value["base"]["sha"], "changed_files": value["changed_files"],
                "body": value.get("body") or ""}

    def state(self, repo, number):
        pr = self.pr(repo, number)
        return {key: pr[key] for key in ("state", "head_sha", "base_sha")}

    def author(self, repo, number):
        return self._api(f"repos/{repo}/pulls/{number}")["user"]["login"]

    def actor(self):
        return self._api("user")["login"]

    def files(self, repo, number):
        values = self._api(f"repos/{repo}/pulls/{number}/files?per_page=100", paginate=True)
        return [{"filename": item["filename"], "patch": item.get("patch")} for item in values]

    def diff(self, repo, number):
        return self._api(f"repos/{repo}/pulls/{number}", accept="application/vnd.github.diff")

    def reviews_for_head(self, repo, number):
        values = self._api(f"repos/{repo}/pulls/{number}/reviews?per_page=100", paginate=True)
        states = {"APPROVED": "APPROVE", "CHANGES_REQUESTED": "REQUEST_CHANGES", "COMMENTED": "COMMENT"}
        return [{"id": item["id"], "author": item["user"]["login"], "commit_id": item["commit_id"],
                 "state": states.get(item["state"], item["state"]), "body": item.get("body") or ""}
                for item in values]

    def reviews(self, repo, number):
        return self.reviews_for_head(repo, number)

    def comments(self, repo, number):
        result = []
        for kind in ("issues", "pulls"):
            values = self._api(f"repos/{repo}/{kind}/{number}/comments?per_page=100", paginate=True)
            result.extend({"id": item["id"], "author": item["user"]["login"],
                           "body": item["body"], "path": item.get("path"),
                           "line": item.get("line")} for item in values)
        return result

    def checks(self, repo, head_sha):
        value = self._api(f"repos/{repo}/commits/{head_sha}/check-runs?per_page=100")
        if value["total_count"] > 100:
            raise ValueError("PR checks inventory exceeds one page")
        result = [{"name": item["name"], "status": item["status"], "conclusion": item["conclusion"]}
                  for item in value["check_runs"]]
        status = self._api(f"repos/{repo}/commits/{head_sha}/status")
        result.extend({"name": item["context"], "status": "completed", "conclusion": item["state"]}
                      for item in status["statuses"])
        if len(result) > 500:
            raise ValueError("PR checks inventory exceeds limit")
        return result

    def post(self, repo, number, head_sha, event, body):
        value = self._api(f"repos/{repo}/pulls/{number}/reviews",
                          post={"commit_id": head_sha, "event": event, "body": body})
        states = {"APPROVED": "APPROVE", "CHANGES_REQUESTED": "REQUEST_CHANGES", "COMMENTED": "COMMENT"}
        return {"id": value["id"], "author": value["user"]["login"],
                "commit_id": value["commit_id"], "state": states.get(value["state"], value["state"]),
                "body": value.get("body") or ""}
