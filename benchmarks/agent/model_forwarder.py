"""A fixed forwarder to the model endpoint, and the only egress an arm gets.

protocol-a1 section 11.4 asks for an explicit allowlist. The previous implementation set
proxy environment variables and declared victory when `pip download` failed -- but
PIP_NO_INDEX makes pip fail without opening a socket, so the negative control could pass
with the network wide open, and Claude Code ignores proxy variables entirely, so nothing
constrained the runner either. It was a request, not a boundary.

This is a boundary. The arm runs under `sandbox-exec` with `(deny network-outbound)` and a
single exception for localhost, which is the only host the profile language will name. This
process listens there and forwards to ONE hardcoded upstream. It is not a general proxy:
there is no CONNECT verb and no way to name a different host, so an arm that reaches it can
talk to the model and to nothing else.
"""
from __future__ import annotations

import http.server
import socketserver
import ssl
import sys
import http.client

UPSTREAM_HOST = "api.deepseek.com"
UPSTREAM_BASE = "/anthropic"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self, method: str) -> None:
        body = self.rfile.read(int(self.headers.get("content-length", 0) or 0))
        conn = http.client.HTTPSConnection(UPSTREAM_HOST, 443,
                                           context=ssl.create_default_context(), timeout=600)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Host"] = UPSTREAM_HOST
        path = self.path if self.path.startswith(UPSTREAM_BASE) else UPSTREAM_BASE + self.path
        try:
            conn.request(method, path, body=body, headers=headers)
            up = conn.getresponse()
        except Exception as exc:                                    # upstream unreachable
            msg = f'{{"type":"error","error":{{"message":"forwarder: {exc}"}}}}'.encode()
            self.send_response(502)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return

        self.send_response(up.status)
        for k, v in up.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        # stream: the model API is server-sent events and must not be buffered whole
        while True:
            chunk = up.read(8192)
            if not chunk:
                break
            self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        conn.close()

    def do_POST(self): self._forward("POST")
    def do_GET(self): self._forward("GET")
    def log_message(self, *a): pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), Handler) as srv:
        print(f"forwarder on 127.0.0.1:{PORT} -> https://{UPSTREAM_HOST}{UPSTREAM_BASE}",
              flush=True)
        srv.serve_forever()
