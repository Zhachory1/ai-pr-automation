#!/usr/bin/env python3
"""One-time Calendar-only OAuth for the restricted Hermes planner profile."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat

SCOPES = ["https://www.googleapis.com/auth/calendar"]
INSPECT_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def require_calendar_scope(granted):
    if set(granted) != set(SCOPES):
        raise ValueError("Google granted unexpected scopes; no token was stored")


def owned_choices(calendars, rules):
    primary = [item for item in calendars if item.get("primary")]
    if len(primary) != 1 or primary[0].get("id", "").lower() != rules["account"].lower():
        raise ValueError("Google account does not match private planner rules")
    return [{"name": item.get("summary", ""), "id": item["id"], "primary": bool(item.get("primary"))}
            for item in calendars if item.get("accessRole") == "owner"]


def verify(calendars, rules):
    owned_choices(calendars, rules)
    targets = [item for item in calendars if item.get("summary") == rules["calendar"]]
    if len(targets) != 1 or targets[0].get("accessRole") != "owner":
        raise ValueError("target calendar name must identify one owned calendar")
    return targets[0]["id"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true", help="list owned calendar metadata without saving a token")
    args = parser.parse_args()
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    source = Path(__file__).with_name("hermes-week-calendar.py")
    spec = importlib.util.spec_from_file_location("hermes_week_calendar", source)
    calendar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(calendar)

    profile = Path.home() / ".hermes/profiles/week-planner"
    client = calendar.private_file(profile / "google_client_secret.json")
    token = profile / "google_token.json"
    if token.exists() or token.is_symlink():
        raise ValueError("planner OAuth token already exists; refusing overwrite")
    rules = calendar.boundary(Path.home() / ".config/ai-pr-automation/week-planner.md")
    flow = InstalledAppFlow.from_client_secrets_file(str(client), INSPECT_SCOPES if args.inspect else SCOPES)
    credentials = flow.run_local_server(port=0, open_browser=True, prompt="consent", access_type="offline")
    granted = set(credentials.granted_scopes or credentials.scopes or [])
    if args.inspect:
        if granted != set(INSPECT_SCOPES):
            raise ValueError("Google granted unexpected inspection scopes; no token was stored")
    else:
        require_calendar_scope(granted)
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    items, page_token = [], None
    while True:
        page = service.calendarList().list(pageToken=page_token, showHidden=True).execute()
        items.extend(page.get("items", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    if args.inspect:
        print(json.dumps(owned_choices(items, rules), sort_keys=True))
        print("Read-only inspection complete. No token saved and no events created.")
        return
    verify(items, rules)
    payload = credentials.to_json()
    fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        token.unlink(missing_ok=True)
        raise
    if token.stat().st_uid != os.getuid() or stat.S_IMODE(token.stat().st_mode) != 0o600:
        raise ValueError("OAuth token permissions are unsafe")
    print("Calendar-only OAuth saved; account and unique owned target verified. No events created.")


if __name__ == "__main__":
    main()
