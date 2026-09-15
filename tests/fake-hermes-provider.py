#!/usr/bin/env python3
import json
import os
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CAPTURE = os.environ.get("CAPTURE", "/capture/request.json")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, body, status=200):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.endswith("/models"):
            self.send_json({"object": "list", "data": [{"id": "gpt-4o-mini", "object": "model"}]})
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))) or b"{}")
        directory = os.path.dirname(CAPTURE)
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=directory, delete=False) as output:
            json.dump({"path": self.path, "body": body}, output, sort_keys=True)
            temporary = output.name
        os.replace(temporary, CAPTURE)
        if not self.path.endswith("/responses"):
            self.send_error(404)
            return
        if "slow" in json.dumps(body).lower():
            time.sleep(5)

        response = {
            "id": "resp_spike",
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "instructions": body.get("instructions"),
            "max_output_tokens": None,
            "model": body.get("model", "gpt-4o-mini"),
            "output": [{
                "id": "msg_spike",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "spike-ok", "annotations": []}],
            }],
            "parallel_tool_calls": True,
            "previous_response_id": None,
            "reasoning": {"effort": None, "summary": None},
            "store": False,
            "temperature": 1.0,
            "text": {"format": {"type": "text"}},
            "tool_choice": "auto",
            "tools": [],
            "top_p": 1.0,
            "truncation": "disabled",
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 2,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 12,
            },
            "metadata": {},
        }
        if not body.get("stream"):
            self.send_json(response)
            return

        events = [
            ("response.created", {"type": "response.created", "response": {**response, "status": "in_progress", "output": [], "usage": None}}),
            ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {"id": "msg_spike", "type": "message", "status": "in_progress", "role": "assistant", "content": []}}),
            ("response.content_part.added", {"type": "response.content_part.added", "item_id": "msg_spike", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}}),
            ("response.output_text.delta", {"type": "response.output_text.delta", "item_id": "msg_spike", "output_index": 0, "content_index": 0, "delta": "spike-ok"}),
            ("response.output_text.done", {"type": "response.output_text.done", "item_id": "msg_spike", "output_index": 0, "content_index": 0, "text": "spike-ok"}),
            ("response.content_part.done", {"type": "response.content_part.done", "item_id": "msg_spike", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "spike-ok", "annotations": []}}),
            ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": response["output"][0]}),
            ("response.completed", {"type": "response.completed", "response": response}),
        ]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for name, event in events:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
