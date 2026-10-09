"""Read prior New York day's inbox metadata through an injected read-only Gmail client."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo("America/New_York")


class InboxOverflow(Exception):
    pass


def day_window(day: date):
    start = datetime.combine(day, time.min, NEW_YORK)
    end = datetime.combine(day + timedelta(days=1), time.min, NEW_YORK)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def previous_day(now: datetime):
    day = now.astimezone(NEW_YORK).date() - timedelta(days=1)
    return day, *day_window(day)


def scan(gmail, now: datetime, account: str, *, limit: int, day: date | None = None):
    if gmail.profile()["emailAddress"].casefold() != account.casefold():
        raise ValueError("Gmail account mismatch")
    start, end = day_window(day) if day is not None else previous_day(now)[1:]
    query = f"after:{start // 1000 - 1} before:{end // 1000}"
    seen, tokens, selected = set(), set(), []
    token = None
    while True:
        page = gmail.list_messages(query, label_ids=("INBOX",), page_token=token)
        for item in page.get("messages", []):
            message_id = item["id"]
            if message_id in seen:
                continue
            seen.add(message_id)
            if len(seen) > limit:
                raise InboxOverflow("daily inbox scan exceeded candidate limit")
            message = gmail.get_message(
                message_id, format="metadata", metadata_headers=("From", "Reply-To", "Subject")
            )
            if message["id"] != message_id or "INBOX" not in message["labelIds"]:
                continue
            if start <= int(message["internalDate"]) < end:
                selected.append({
                    "id": message_id,
                    "threadId": message["threadId"],
                    "internalDate": int(message["internalDate"]),
                    "snippet": message.get("snippet", ""),
                    "headers": [h for h in message.get("payload", {}).get("headers", [])
                                if h["name"].lower() in ("from", "reply-to", "subject")],
                })
        token = page.get("nextPageToken")
        if not token:
            return selected
        if token in tokens:
            raise ValueError("Gmail pagination repeated a page token")
        tokens.add(token)
