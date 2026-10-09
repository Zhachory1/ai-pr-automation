"""Parse a tool-free Sol draft result; controller owns recipients and effects."""

import json
import os
from pathlib import Path
import stat
import subprocess

from hermes_inbox_luna import _checked_binary, _profile_home, _stream_result


def read_voice() -> str:
    """Load the approved small persona source, without copying it into Git or shared memory."""
    path = Path.home() / "private-docs/inbox/zhachory_volker_persona.md"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise ValueError("private voice source unavailable or linked") from error
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 8000:
            raise ValueError("private voice source must be owner-owned and bounded")
        return source.read()


def draft(hermes_bin, workdir: Path, route: str, thread: list, voice: str) -> str:
    if (route not in ("job", "help") or not isinstance(thread, list) or not 0 < len(thread) <= 40
            or not isinstance(voice, str) or not voice.strip() or len(voice) > 8000
            or sum(len(item["text"]) for item in thread) > 32000):
        raise ValueError("Sol context is missing or exceeds limit")
    projected = []
    for message in thread:
        headers = {h["name"].lower(): h["value"] for h in message["headers"]
                   if h["name"].lower() in ("from", "subject")}
        projected.append({"sender": headers.get("from", "")[:512],
                          "subject": headers.get("subject", "")[:512], "text": message["text"]})
    env = {"HOME": str(Path.home()), "HERMES_HOME": str(_profile_home("inbox-sol")),
           "HERMES_PROFILE": "inbox-sol", "PATH": os.defpath, "PYTHONUTF8": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [_checked_binary(str(hermes_bin)), "chat", "--query-file", "-", "--oneshot",
         "--format", "stream-json", "--max-turns", "1", "--run-budget", "180"],
        input=json.dumps({"route": route, "thread": projected, "voice": voice}, ensure_ascii=False),
        capture_output=True, text=True, cwd=workdir, env=env, timeout=210,
    )
    if result.returncode:
        raise RuntimeError("Sol drafting failed")
    return parse_draft(_stream_result(result.stdout))


def parse_draft(output: str) -> str:
    if not isinstance(output, str) or len(output) > 12000:
        raise ValueError("invalid Sol draft")
    try:
        value = json.loads(output, object_pairs_hook=lambda pairs: pairs)
    except json.JSONDecodeError as error:
        raise ValueError("invalid Sol draft") from error
    if (not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], tuple)
            or len(value[0]) != 2 or value[0][0] != "body"
            or not isinstance(value[0][1], str) or not value[0][1].strip()
            or len(value[0][1]) > 6000 or "\x00" in value[0][1]):
        raise ValueError("invalid Sol draft")
    return value[0][1]
