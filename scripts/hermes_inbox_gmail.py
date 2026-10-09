"""Read-only Gmail API adapter; never writes messages or refreshes token files."""

import base64
import binascii
import json
import os
from pathlib import Path
import re
import stat


READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
HEADERS = ("From", "Reply-To", "Subject")
RECEIPT_HEADERS = ("From", "To", "Subject", "In-Reply-To", "References", "Message-ID")
THREAD_HEADERS = {"from", "reply-to", "subject", "message-id", "references", "in-reply-to"}
THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _build(token):
    import httplib2
    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    credentials = Credentials.from_authorized_user_info(token)
    return build("gmail", "v1", http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=30)),
                 cache_discovery=False)


def _load_token(token_path, scopes):
    path = Path(token_path)
    parent = path.parent.lstat()
    if (path.parent.is_symlink() or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700):
        raise ValueError("Gmail token must be in an owner-only directory and file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("Gmail token must be in an owner-only directory and file")
        token = json.load(source)
    if set(token.get("scopes", [])) != set(scopes) or not token.get("refresh_token"):
        raise ValueError("Gmail credential scopes or refresh token differ from contract")
    return token


def connect(token_path):
    return GmailReadClient(_build(_load_token(token_path, {READ_SCOPE})))


class GmailReadClient:
    def __init__(self, api):
        self.api = api

    def profile(self):
        response = self.api.users().getProfile(userId="me", fields="emailAddress").execute()
        return {"emailAddress": response["emailAddress"]}

    def list_messages(self, query, *, label_ids, page_token=None):
        response = self.api.users().messages().list(
            userId="me", q=query, labelIds=list(label_ids), pageToken=page_token,
            maxResults=500, fields="messages/id,nextPageToken",
        ).execute()
        return {"messages": [{"id": item["id"]} for item in response.get("messages", [])],
                "nextPageToken": response.get("nextPageToken")}

    def get_message(self, message_id, *, format, metadata_headers):
        if format != "metadata" or tuple(metadata_headers) not in (HEADERS, RECEIPT_HEADERS):
            raise ValueError("Gmail adapter permits metadata and approved headers only")
        response = self.api.users().messages().get(
            userId="me", id=message_id, format="metadata", metadataHeaders=list(metadata_headers),
            fields="id,threadId,internalDate,labelIds,snippet,payload/headers",
        ).execute()
        return {"id": response["id"], "threadId": response["threadId"],
                "internalDate": response["internalDate"], "labelIds": response.get("labelIds", []),
                "snippet": response.get("snippet", ""),
                "payload": {"headers": [h for h in response.get("payload", {}).get("headers", [])
                                        if h["name"].lower() in {name.lower() for name in metadata_headers}]}}

    def get_thread(self, thread_id):
        if not isinstance(thread_id, str) or not THREAD_ID.fullmatch(thread_id):
            raise ValueError("invalid Gmail thread identity")
        summary = self.api.users().threads().get(
            userId="me", id=thread_id, format="metadata", fields="id,messages(id,threadId,internalDate,sizeEstimate)",
        ).execute()
        headers = summary.get("messages", [])
        if (summary.get("id") != thread_id or not 0 < len(headers) <= 40
                or any(item.get("threadId") != thread_id or not isinstance(item.get("internalDate"), str)
                       or type(item.get("sizeEstimate")) is not int
                       or not 0 <= item["sizeEstimate"] <= 128000 for item in headers)
                or sum(item["sizeEstimate"] for item in headers) > 256000):
            raise ValueError("Gmail thread identity or raw size exceeds limit")
        response = self.api.users().threads().get(
            userId="me", id=thread_id, format="full",
            fields="id,messages(id,threadId,internalDate,payload)",
        ).execute()
        messages = response.get("messages", [])
        if (response.get("id") != thread_id
                or [(m.get("id"), m.get("threadId"), m.get("internalDate")) for m in messages]
                   != [(m.get("id"), m.get("threadId"), m.get("internalDate")) for m in headers]):
            raise ValueError("Gmail thread changed after metadata preflight")

        parts_seen = 0
        def plain(part, depth=0):
            nonlocal parts_seen
            parts_seen += 1
            if depth > 8 or parts_seen > 100:
                raise ValueError("Gmail MIME structure exceeds limit")
            if part.get("filename") or part.get("body", {}).get("attachmentId"):
                return ""
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if len(data) > 22000:
                    raise ValueError("Gmail plain-text part exceeds limit")
                try:
                    decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
                except (binascii.Error, ValueError) as error:
                    raise ValueError("invalid Gmail plain-text part") from error
                if len(decoded) > 16000:
                    raise ValueError("Gmail plain-text part exceeds limit")
                return decoded.decode("utf-8", errors="replace")
            return "\n".join(filter(None, (plain(child, depth + 1) for child in part.get("parts", []))))

        projected = []
        header_chars = 0
        for message in messages:
            if message.get("threadId") != thread_id:
                raise ValueError("Gmail message is outside selected thread")
            payload = message.get("payload", {})
            body = plain(payload)
            if not body.strip():
                raise ValueError("Gmail thread contains no plain-text body")
            headers = [h for h in payload.get("headers", []) if h["name"].lower() in THREAD_HEADERS]
            if len(headers) > 12 or any(len(h["value"]) > 512 for h in headers):
                raise ValueError("Gmail thread headers exceed limit")
            header_chars += sum(len(h["value"]) for h in headers)
            if header_chars > 8192:
                raise ValueError("Gmail thread headers exceed limit")
            projected.append({"id": message["id"], "internalDate": message["internalDate"],
                              "headers": headers, "text": body})
        if sum(len(item["text"]) for item in projected) > 32000:
            raise ValueError("Gmail thread text exceeds limit")
        return sorted(projected, key=lambda item: (int(item["internalDate"]), item["id"]))
