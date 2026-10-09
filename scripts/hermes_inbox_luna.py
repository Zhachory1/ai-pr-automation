"""Run one tool-free Luna triage turn on bounded Gmail metadata."""

from functools import lru_cache
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import yaml

from hermes_inbox_admit import parse_route


@lru_cache(maxsize=2)
def _checked_binary(binary: str) -> str:
    path = Path(binary)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("pinned Hermes CLI is unavailable")
    result = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=8)
    if result.returncode or not re.search(r"^Hermes Agent v0\.21\.5\b", result.stdout, re.MULTILINE):
        raise ValueError("pinned Hermes CLI version changed")
    return str(path)


def _profile_home(name="inbox-luna") -> Path:
    models = {"inbox-luna": "gpt-5.6-luna", "inbox-sol": "gpt-5.6-sol"}
    if name not in models:
        raise ValueError("unknown restricted inbox profile")
    profile = Path.home() / ".hermes/profiles" / name
    try:
        info = profile.lstat()
    except FileNotFoundError as error:
        raise ValueError("restricted inbox profile is not installed") from error
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("restricted inbox profile must be owner-only")
    files = {}
    for filename in ("config.yaml", "SOUL.md"):
        fd = os.open(profile / filename, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("restricted inbox profile file must be owner-only")
            files[filename] = source.read()
    config = yaml.safe_load(files["config.yaml"])
    agent = config.get("agent", {}) if isinstance(config, dict) else {}
    disabled = set(agent.get("disabled_toolsets", []))
    if (config.get("model", {}).get("provider") != "openai-codex"
            or config["model"].get("default") != models[name]
            or config.get("plugins", {}).get("enabled") != []
            or config.get("mcp_servers") not in (None, {})
            or config.get("platform_toolsets", {}).get("cli") != ["no_mcp"]
            or config.get("memory", {}).get("memory_enabled") is not False
            or agent.get("max_turns") != 1
            or not {"terminal", "file", "code_execution", "browser", "web", "skills", "memory", "kanban"} <= disabled):
        raise ValueError("restricted inbox profile changed")
    return profile


def classify(hermes_bin, workdir: Path, day: str, message: dict) -> str:
    headers = {h["name"].lower(): h["value"] for h in message["headers"]
               if h["name"].lower() in ("from", "subject")}
    payload = {"source_day": day, "sender": headers.get("from", "")[:512],
               "subject": headers.get("subject", "")[:512], "snippet": message["snippet"][:512]}
    env = {"HOME": str(Path.home()), "HERMES_HOME": str(_profile_home()),
           "HERMES_PROFILE": "inbox-luna", "PATH": os.defpath, "PYTHONUTF8": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [_checked_binary(str(hermes_bin)), "chat", "--query-file", "-", "--oneshot",
         "--format", "stream-json", "--max-turns", "1", "--run-budget", "120"],
        input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
        cwd=workdir, env=env, timeout=150,
    )
    if result.returncode:
        raise RuntimeError("Luna classification failed")
    return parse_route(_stream_result(result.stdout))


def _stream_result(output: str) -> str:
    if len(output) > 64000:
        raise ValueError("inbox model output exceeded limit")
    terminal = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("invalid inbox model event stream") from error
        if not isinstance(event, dict) or event.get("type") not in ("system", "text", "result"):
            raise ValueError("unexpected inbox model event or tool call")
        if event["type"] == "result":
            terminal.append(event)
    if len(terminal) != 1 or terminal[0].get("exit_code") != 0:
        raise ValueError("inbox model result missing or failed")
    return terminal[0].get("text")
