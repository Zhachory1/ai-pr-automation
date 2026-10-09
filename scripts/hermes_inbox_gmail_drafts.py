"""Gmail Drafts-only transport; no call to users.messages.send or drafts.send."""

import base64
import re

from hermes_inbox_gmail import READ_SCOPE, _build as _google_build, _load_token


COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
HEADERS = ("From", "To", "Subject", "In-Reply-To", "References", "Message-ID")


def _build(token):
    return _google_build(token)


def connect(token_path):
    return GmailDraftClient(_build(_load_token(token_path, {READ_SCOPE, COMPOSE_SCOPE})))


class GmailDraftClient:
    def __init__(self, api):
        self.api = api

    def profile(self):
        value = self.api.users().getProfile(userId="me", fields="emailAddress").execute()
        return {"emailAddress": value["emailAddress"]}

    def create(self, mime: bytes, thread_id: str):
        if not isinstance(mime, bytes) or not mime or not isinstance(thread_id, str) or not IDENTITY.fullmatch(thread_id):
            raise ValueError("invalid Gmail draft target")
        raw = base64.urlsafe_b64encode(mime).decode("ascii").rstrip("=")
        response = self.api.users().drafts().create(
            userId="me", body={"message": {"raw": raw, "threadId": thread_id}},
        ).execute()
        if not isinstance(response.get("id"), str) or not IDENTITY.fullmatch(response["id"]) \
                or response.get("message", {}).get("threadId") != thread_id:
            raise ValueError("Gmail draft creation outcome requires reconciliation")
        return response["id"]

    def get(self, draft_id: str):
        if not isinstance(draft_id, str) or not IDENTITY.fullmatch(draft_id):
            raise ValueError("invalid Gmail draft ID")
        response = self.api.users().drafts().get(
            userId="me", id=draft_id, format="metadata",
            fields="id,message(id,threadId,labelIds,payload/headers)",
        ).execute()
        message = response["message"]
        return {"id": response["id"], "message": {"id": message["id"],
                "threadId": message["threadId"], "labelIds": message.get("labelIds", []),
                "headers": [h for h in message.get("payload", {}).get("headers", [])
                            if h["name"].lower() in {name.lower() for name in HEADERS}]}}
