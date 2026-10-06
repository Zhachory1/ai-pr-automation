#!/usr/bin/env python3
"""Narrow Google Calendar MCP boundary for the conversational Hermes week planner."""
import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def private_file(path):
    path = Path(path)
    info, parent = path.lstat(), path.parent.lstat()
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077):
        raise ValueError("planner data must be owner-only")
    return path


def boundary(path):
    text = private_file(path).read_text(encoding="utf-8")
    match = re.search(r"^```json\s*\n(.*?)\n```\s*$", text, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError("private calendar boundary is missing")
    value = json.loads(match.group(1))
    if not all(isinstance(value.get(key), str) and value[key] for key in ("account", "calendar", "timezone")):
        raise ValueError("invalid private calendar boundary")
    ZoneInfo(value["timezone"])
    return value


class CalendarGuard:
    def __init__(self, root, google, now=None):
        self.root, self.google = Path(root), google
        self.rules = boundary(self.root / "week-planner.md")
        self.notes = self.root / "week-planner-notes.md"
        self.zone = ZoneInfo(self.rules["timezone"])
        self.now = now or datetime.now(self.zone)
        self._calendars = self._target_id = None

    def calendars(self):
        if self._calendars is None:
            items = self.google.list_calendars()
            primary = [item for item in items if item.get("primary")]
            if len(primary) != 1 or primary[0].get("id", "").lower() != self.rules["account"].lower():
                raise ValueError("authenticated account does not match private rules")
            targets = [item for item in items if item.get("summary") == self.rules["calendar"]]
            if len(targets) != 1 or targets[0].get("accessRole") != "owner":
                raise ValueError("private calendar must uniquely identify an owned calendar")
            self._calendars, self._target_id = items, targets[0]["id"]
        return self._calendars

    def span(self, start, end):
        def parse(raw):
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("times must include a timezone offset")
            return parsed.astimezone(self.zone)
        first, last = parse(start), parse(end)
        if (first.astimezone(timezone.utc) >= last.astimezone(timezone.utc)
                or last.astimezone(timezone.utc) - first.astimezone(timezone.utc) > timedelta(hours=12)):
            raise ValueError("invalid planner event duration")
        if first < self.now - timedelta(minutes=5) or last > self.now + timedelta(days=90):
            raise ValueError("planner event is outside the supported future window")
        return first, last

    def context(self, monday):
        week = date.fromisoformat(monday)
        if week.weekday() != 0:
            raise ValueError("context must start on a Monday")
        start = datetime.combine(week, datetime.min.time(), self.zone)
        end = start + timedelta(days=7, hours=6)
        if end < self.now or end > self.now + timedelta(days=90):
            raise ValueError("week is outside the supported window")
        events = []
        for item in self.calendars():
            for event in self.google.list_events(item["id"], start, end):
                if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                    continue
                marker = event.get("extendedProperties", {}).get("private", {})
                events.append({"calendar": item["summary"], "summary": str(event.get("summary") or "(busy)")[:200],
                               "start": event["start"], "end": event["end"],
                               "owned": item["id"] == self._target_id and marker.get("source") == "week-planner-v1",
                               "id": event["id"] if item["id"] == self._target_id and marker.get("source") == "week-planner-v1" else None})
                if len(events) > 500:
                    raise ValueError("too many calendar events to plan safely")
        rules = private_file(self.notes).read_text(encoding="utf-8")
        if len(rules) > 20000:
            raise ValueError("private planning rules are too large")
        return {"timezone": self.rules["timezone"], "rules": rules,
                "events": sorted(events, key=lambda item: item["start"])}

    def _available(self, start, end, *, excluding=None):
        ids = [item["id"] for item in self.calendars() if item["id"] != self._target_id]
        if self.google.busy(ids, start, end):
            raise ValueError("requested time is busy on another calendar")
        for event in self.google.list_events(self._target_id, start, end):
            if event.get("id") == excluding or event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                continue
            raise ValueError("requested time is busy on the private calendar")

    @contextmanager
    def write_lock(self):
        fd = os.open(self.root / ".calendar-write.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("unsafe calendar lock")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def create(self, key, summary, start, end):
        if not re.fullmatch(r"[a-z0-9._:-]{5,100}", key) or not isinstance(summary, str) or not 0 < len(summary) <= 120:
            raise ValueError("invalid planner event identity or title")
        first, last = self.span(start, end)
        self.calendars()
        event_id = "pl" + hashlib.sha256(f"{self._target_id}|{key}".encode()).hexdigest()[:30]
        marker = {"source": "week-planner-v1", "key": key}
        with self.write_lock():
            existing = self.google.get(self._target_id, event_id)
            if existing:
                if existing.get("extendedProperties", {}).get("private", {}) != marker:
                    raise ValueError("event ID collision with an unowned event")
                return {"id": event_id, "status": "deleted_by_user" if existing.get("status") == "cancelled" else "existing"}
            self._available(first, last)
            body = {"id": event_id, "summary": summary, "visibility": "private", "start": {"dateTime": first.isoformat(), "timeZone": self.rules["timezone"]},
                    "end": {"dateTime": last.isoformat(), "timeZone": self.rules["timezone"]}, "extendedProperties": {"private": marker}}
            inserted = self.google.insert(self._target_id, body)
            return {"id": event_id, "status": "created" if inserted else "existing"}

    def move(self, event_id, start, end):
        if not re.fullmatch(r"pl[0-9a-f]{30}", event_id):
            raise ValueError("event is not planner-owned")
        first, last = self.span(start, end)
        self.calendars()
        with self.write_lock():
            event = self.google.get(self._target_id, event_id)
            if not event or event.get("extendedProperties", {}).get("private", {}).get("source") != "week-planner-v1":
                raise ValueError("event is not planner-owned")
            self._available(first, last, excluding=event_id)
            self.google.patch(self._target_id, event_id, first.isoformat(), last.isoformat(), event["etag"])
            return {"id": event_id, "status": "moved"}

    def append_rule(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 500 or "\n" in text or "\r" in text:
            raise ValueError("standing preference must be one short line")
        path = private_file(self.notes)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(fd, "a", encoding="utf-8") as output:
            fcntl.flock(output.fileno(), fcntl.LOCK_EX)
            output.write("\n- " + text.strip() + "\n")
            output.flush()
            os.fsync(output.fileno())
        return {"status": "recorded"}


class GoogleCalendar:
    def __init__(self, token):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        credentials = Credentials.from_authorized_user_file(str(private_file(token)))
        if not credentials.has_scopes(["https://www.googleapis.com/auth/calendar"]):
            raise ValueError("calendar OAuth scope is missing")
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials.valid:
            raise ValueError("calendar OAuth is not authenticated")
        self.api = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def list_calendars(self):
        items, token = [], None
        while True:
            page = self.api.calendarList().list(pageToken=token, showHidden=True).execute()
            items += page.get("items", [])
            token = page.get("nextPageToken")
            if not token:
                return items

    def list_events(self, calendar_id, start, end):
        items, token = [], None
        while True:
            page = self.api.events().list(calendarId=calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
                                          singleEvents=True, maxResults=2500, pageToken=token).execute()
            for event in page.get("items", []):
                begin, finish = event.get("start", {}), event.get("end", {})
                if "dateTime" in begin and "dateTime" in finish:
                    items.append({**event, "start": begin["dateTime"], "end": finish["dateTime"]})
                elif "date" in begin and "date" in finish:
                    midnight = datetime.min.time()
                    items.append({**event, "start": datetime.combine(date.fromisoformat(begin["date"]), midnight, start.tzinfo).isoformat(),
                                  "end": datetime.combine(date.fromisoformat(finish["date"]), midnight, start.tzinfo).isoformat()})
            token = page.get("nextPageToken")
            if not token:
                return items

    def busy(self, ids, start, end):
        result = []
        for index in range(0, len(ids), 50):
            batch = ids[index:index+50]
            page = self.api.freebusy().query(body={"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                                                   "items": [{"id": item} for item in batch]}).execute()
            for calendar_id in batch:
                info = page.get("calendars", {}).get(calendar_id)
                if info is None or info.get("errors"):
                    raise ValueError("cannot read all calendar availability")
                result.extend(info.get("busy", []))
        return result

    def get(self, calendar_id, event_id):
        from googleapiclient.errors import HttpError
        try:
            return self.api.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except HttpError as error:
            if error.resp.status == 404:
                return None
            raise

    def insert(self, calendar_id, body):
        from googleapiclient.errors import HttpError
        try:
            self.api.events().insert(calendarId=calendar_id, body=body, sendUpdates="none").execute()
            return True
        except HttpError as error:
            if error.resp.status != 409:
                raise
            existing = self.get(calendar_id, body["id"])
            if not existing or existing.get("extendedProperties", {}).get("private", {}) != body["extendedProperties"]["private"]:
                raise ValueError("event ID collision with an unowned event") from error
            return False

    def patch(self, calendar_id, event_id, start, end, etag):
        request = self.api.events().patch(calendarId=calendar_id, eventId=event_id,
                                          body={"start": {"dateTime": start}, "end": {"dateTime": end}}, sendUpdates="none")
        request.headers["If-Match"] = etag
        request.execute()


def main():
    import mcp.types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    root = Path.home() / ".config/ai-pr-automation"
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    guard = CalendarGuard(root, GoogleCalendar(hermes_home / "google_token.json"))
    tools = [types.Tool(name="week_context", description="Read owner-only planning rules and one week's events across all calendars.",
                        inputSchema={"type": "object", "properties": {"monday": {"type": "string"}}, "required": ["monday"]}),
             types.Tool(name="create_block", description="Create one private, planner-owned block on the verified calendar; repeated key preserves user edits.",
                        inputSchema={"type": "object", "properties": {"key": {"type": "string"}, "summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["key", "summary", "start", "end"]}),
             types.Tool(name="move_block", description="Move one existing planner-owned block; cannot modify other events or calendars.",
                        inputSchema={"type": "object", "properties": {"event_id": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["event_id", "start", "end"]}),
             types.Tool(name="remember_rule", description="Append one standing preference only when the human explicitly asks to change a recurring rule.",
                        inputSchema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})]

    async def list_tools(_context, _params):
        return types.ListToolsResult(tools=tools)

    async def call_tool(_context, params):
        commands = {"week_context": guard.context, "create_block": guard.create,
                    "move_block": guard.move, "remember_rule": guard.append_rule}
        try:
            if params.name not in commands or not isinstance(params.arguments, dict):
                raise ValueError("unsupported planner request")
            result = commands[params.name](**params.arguments)
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, default=str))])
        except ValueError as error:
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(error))], isError=True)
        except Exception:
            return types.CallToolResult(content=[types.TextContent(type="text", text="calendar operation failed")], isError=True)

    server = Server("week-calendar", on_list_tools=list_tools, on_call_tool=call_tool)
    async def serve():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    asyncio.run(serve())


if __name__ == "__main__":
    main()
