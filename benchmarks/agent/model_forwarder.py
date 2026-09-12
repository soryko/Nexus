"""A fixed forwarder to the model endpoint, and the only egress an arm gets.

protocol-a1 section 11.4 asks for an explicit allowlist. The first implementation set proxy
environment variables and declared victory when `pip download` failed -- but PIP_NO_INDEX
makes pip fail without opening a socket, so the negative control could pass with the network
wide open, and Claude Code ignores proxy variables entirely, so nothing constrained the
runner either. It was a request, not a boundary.

The second implementation was a boundary on the *host* axis only: `sandbox-exec` denied every
remote but localhost, and this process forwarded to one hardcoded upstream with no CONNECT
verb. That still forwarded ANY body to that upstream. An arm holding Bash can post its own
request to this port -- the API key is in its environment -- and ask the provider for a
server-side search tool. `--disallowedTools` governs what Claude Code offers the model; it
does not inspect a request the arm composes itself. The route was reachable. (Reachable is
the claim here; nothing in the saved traces shows an arm taking it.)

So the allowlist is now three-layered:

  port    the sandbox admits `localhost:<this port>` and nothing else, so the arm cannot
          reach another local listener or stand up a proxy of its own on a second port.
  route   only the model-inference routes are accepted. Anything else -- and any method
          other than POST -- is refused here, before a socket to the upstream is opened.
  tools   every `tools` entry must be a client-side tool definition. A provider-executed
          tool is named by its `type` (`web_search_*`, `code_execution_*`, `bash_*`, ...),
          and `mcp_servers` asks the provider to call out on its own; both are refused.

Refusals are counted and written to stderr, so the run has evidence either way.
"""
from __future__ import annotations

import http.server
import json
import re
import socketserver
import ssl
import sys
import http.client
from collections import Counter

UPSTREAM_HOST = "api.deepseek.com"
UPSTREAM_BASE = "/anthropic"
DEFAULT_PORT = 8899
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"}

# Inference only. No model listing, no files, no batches, no admin surface.
ALLOWED_ROUTES = (
    re.compile(r"^/v1/messages(\?.*)?$"),
    re.compile(r"^/v1/messages/count_tokens(\?.*)?$"),
)
MAX_BODY = 32 * 1024 * 1024

# A client-side tool carries a name and an input schema and no `type`, or `type: custom`.
# A provider-executed tool is selected BY its type, so the type is the thing to gate on.
CLIENT_TOOL_TYPES = {None, "custom"}
REFUSALS: Counter = Counter()


def inspect_body(raw: bytes) -> str | None:
    """-> reason the request is refused, or None to forward it.

    A body that is not JSON is forwarded: this is a forwarder, not a schema validator, and
    the upstream will reject what it cannot parse. What is gated is the specific, verified
    way an arm could obtain provider-side retrieval.
    """
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("mcp_servers"):
        return "mcp_servers: provider-side connectors are not available to an arm"
    for tool in payload.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        ttype = tool.get("type")
        if ttype not in CLIENT_TOOL_TYPES:
            return (f"tools[].type={ttype!r}: provider-executed tools are not available "
                    f"to an arm; only client-side tool definitions are forwarded")
    return None


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _refuse(self, status: int, reason: str) -> None:
        REFUSALS[reason.split(":")[0]] += 1
        print(f"forwarder REFUSED {self.command} {self.path}: {reason}",
              file=sys.stderr, flush=True)
        msg = json.dumps({"type": "error",
                          "error": {"type": "forwarder_refused", "message": reason}}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def _route(self) -> str | None:
        """Normalise to an upstream path, or None if the route is not allowed."""
        path = self.path
        if path.startswith(UPSTREAM_BASE):
            path = path[len(UPSTREAM_BASE):] or "/"
        return path if any(r.match(path) for r in ALLOWED_ROUTES) else None

    def _forward(self, method: str) -> None:
        if method != "POST":
            return self._refuse(405, f"method {method} is not forwarded")
        route = self._route()
        if route is None:
            return self._refuse(403, f"route not on the allowlist: {self.path}")
        length = int(self.headers.get("content-length", 0) or 0)
        if length > MAX_BODY:
            return self._refuse(413, "body too large")
        body = self.rfile.read(length)
        refusal = inspect_body(body)
        if refusal:
            return self._refuse(403, refusal)

        conn = http.client.HTTPSConnection(UPSTREAM_HOST, 443,
                                           context=ssl.create_default_context(), timeout=600)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Host"] = UPSTREAM_HOST
        try:
            conn.request(method, UPSTREAM_BASE + route, body=body, headers=headers)
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
    def do_PUT(self): self._forward("PUT")
    def do_DELETE(self): self._forward("DELETE")
    def do_PATCH(self): self._forward("PATCH")
    def do_HEAD(self): self._forward("HEAD")
    def do_OPTIONS(self): self._forward("OPTIONS")
    def do_CONNECT(self): self._refuse(405, "CONNECT is not implemented")
    def log_message(self, *a): pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    # parsed here, not at import: `inspect_body` is imported by the boundary validator,
    # which has its own argv
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    with Server(("127.0.0.1", PORT), Handler) as srv:
        print(f"forwarder on 127.0.0.1:{PORT} -> https://{UPSTREAM_HOST}{UPSTREAM_BASE} "
              f"(POST {', '.join(r.pattern for r in ALLOWED_ROUTES)}; "
              f"client-side tool definitions only)", flush=True)
        srv.serve_forever()
