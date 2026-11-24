"""Capture the outbound /v1/messages request body, then refuse it.

The run is not meant to succeed: the tool inventory is decided before the first request, so
the first body carries the answer. Refusing keeps the probe free and offline.
"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

OUT = sys.argv[1]

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        with open(OUT, "ab") as f:
            f.write(json.dumps({"path": self.path, "body": body.decode("utf-8", "replace")}).encode() + b"\n")
        payload = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                                                        "message": "probe: captured"}}).encode()
        self.send_response(400)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", 8787), H).serve_forever()
