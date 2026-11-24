"""A SCRIPTED loopback upstream, for measuring what the CLI counts as a turn.

The existing stub (`validate_boundary._Stub`) answers one non-streaming message and exists
to show that an accepted request reaches an upstream intact. It cannot drive an agent loop.
This one can: it replays a fixed script of assistant turns as server-sent events, one turn
per request, so a real `claude` process runs a real loop against a model that costs nothing
and never leaves the host.

It is reached the same way the existing stub is -- `A1_FORWARDER_STUB_UPSTREAM`, which only
accepts a loopback address and which a sandboxed arm cannot set -- so the forwarder's own
allowlist still stands between the CLI and the network.

Every request body is recorded, so the number of MODEL REQUESTS is observed rather than
inferred from the envelope that is the thing under measurement.

    python3 stub_turns.py --port 8903 --script <name> [--log <path>]
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import signal
import socketserver
import sys
import uuid

def _texts(body: dict, role: str | None, system: bool = False) -> list[str]:
    """Every text block of the requested role, in order. `system` reads the system array."""
    if system:
        blocks = body.get("system") or []
        return [b.get("text", "") for b in blocks if isinstance(b, dict)]
    out = []
    for m in body.get("messages") or []:
        if role and m.get("role") != role:
            continue
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out += [b.get("text", "") for b in c
                    if isinstance(b, dict) and b.get("type") == "text"]
    return out


def _text_digests(body: dict, role: str | None, system: bool = False) -> list[str]:
    return [hashlib.sha256(t.encode()).hexdigest()[:16]
            for t in _texts(body, role, system) if t]


def _first_text(body: dict, role: str | None) -> str:
    t = _texts(body, role)
    return t[0] if t else ""


# Each script is a list of assistant turns; each turn is a list of blocks.
#   {"text": "..."}                     one text block
#   {"tool": "Bash", "input": {...}}    one tool_use block
# A turn with any tool block ends with stop_reason `tool_use`, which is what makes the CLI
# execute it and come back; a text-only turn ends the loop.
SCRIPTS: dict[str, list[list[dict]]] = {
    # one tool per assistant message, three times, then a final answer
    "sequential": [
        [{"tool": "Bash", "input": {"command": "echo one"}}],
        [{"tool": "Bash", "input": {"command": "echo two"}}],
        [{"tool": "Bash", "input": {"command": "echo three"}}],
        [{"text": "done"}],
    ],
    # THREE tools in ONE assistant message, then a final answer
    "parallel": [
        [{"tool": "Bash", "input": {"command": "echo a"}},
         {"tool": "Bash", "input": {"command": "echo b"}},
         {"tool": "Bash", "input": {"command": "echo c"}}],
        [{"text": "done"}],
    ],
    # a tool that fails, then one that works, then a final answer
    "error_recovery": [
        [{"tool": "Bash", "input": {"command": "exit 7"}}],
        [{"tool": "Bash", "input": {"command": "echo recovered"}}],
        [{"text": "done"}],
    ],
    # An A3-SHAPED run: consult the memory service, edit source, run the test, answer.
    # Used by `rehearse_a3_production.py` to drive the real runner with no model. The tool
    # NAMES matter -- the compliance scorer keys on `mcp__nexus__*` for delivery and on an
    # Edit within the task's edit scope for ordering -- so this is not a generic script.
    "a3_consult_edit_test": [
        [{"tool": "mcp__nexus__search",
          "input": {"query": "click option parameter default flag"}}],
        [{"tool": "mcp__nexus__get", "input": {"memory_id": "1"}}],
        [{"tool": "Bash", "input": {"command": "ls src/click/core.py"}}],
        [{"tool": "Bash",
          "input": {"command": "PYTHONPATH=src $A2_PYTHON -m pytest tests -q -x || true"}}],
        [{"text": "DONE. No source change was made; this is a scripted stub."}],
    ],
    # no tool at all
    "final_only": [
        [{"text": "done"}],
    ],
    # text alongside a tool in the same assistant message
    "text_with_tool": [
        [{"text": "I will look."}, {"tool": "Bash", "input": {"command": "echo look"}}],
        [{"text": "done"}],
    ],
    # the reported anomaly, reproduced deliberately: FEWER assistant messages than the cap,
    # but more tool calls than the cap. If `num_turns` counted what the cap counts, it could
    # not come back above the cap on a run that completed normally.
    "overshoot": [
        [{"tool": "Bash", "input": {"command": f"echo {i}{c}"}} for c in "abc"]
        for i in range(3)
    ] + [[{"text": "done"}]],
    # more turns than any cap under test, to observe what the cap counts
    "runaway": [[{"tool": "Bash", "input": {"command": f"echo {i}"}}] for i in range(200)],
    # the same, but TWO tools per assistant message. This is the pair that separates a cap
    # on model responses from a cap on tool calls: `runaway` cannot, because one tool per
    # message makes the two counts equal.
    "runaway_parallel": [[{"tool": "Bash", "input": {"command": f"echo {i}a"}},
                          {"tool": "Bash", "input": {"command": f"echo {i}b"}}]
                         for i in range(200)],
}


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def stream(turn: list[dict], model: str) -> bytes:
    mid = f"msg_{uuid.uuid4().hex[:24]}"
    has_tool = any("tool" in b for b in turn)
    out = [sse("message_start", {"type": "message_start", "message": {
        "id": mid, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 1, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}}})]
    for i, block in enumerate(turn):
        if "text" in block:
            out.append(sse("content_block_start", {
                "type": "content_block_start", "index": i,
                "content_block": {"type": "text", "text": ""}}))
            out.append(sse("content_block_delta", {
                "type": "content_block_delta", "index": i,
                "delta": {"type": "text_delta", "text": block["text"]}}))
        else:
            out.append(sse("content_block_start", {
                "type": "content_block_start", "index": i,
                "content_block": {"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:20]}",
                                  "name": block["tool"], "input": {}}}))
            out.append(sse("content_block_delta", {
                "type": "content_block_delta", "index": i,
                "delta": {"type": "input_json_delta",
                          "partial_json": json.dumps(block["input"])}}))
        out.append(sse("content_block_stop", {"type": "content_block_stop", "index": i}))
    out.append(sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use" if has_tool else "end_turn",
                  "stop_sequence": None},
        "usage": {"output_tokens": 7}}))
    out.append(sse("message_stop", {"type": "message_stop"}))
    return b"".join(out)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    script: list[list[dict]] = []
    served = 0
    requests: list[dict] = []
    instance_token: str = ""

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", 0) or 0))
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        if self.path.rstrip("/").endswith("count_tokens"):
            return self._json({"input_tokens": 11})
        # The CLI issues requests that are NOT the agent loop -- it asked this stub for a
        # session title, and that request silently ate the script's first turn until it was
        # separated here. A loop request in this configuration always carries the client-side
        # tool definitions; a request with none cannot be one. Both are logged, and only a
        # loop request advances the script.
        scripted = bool(body.get("tools"))
        # What the request itself says, recorded before anything is answered.
        Handler.requests.append({
            "n": len(Handler.requests) + 1,
            "scripted": scripted,
            "path": self.path,
            "messages": len(body.get("messages") or []),
            "roles": [m.get("role") for m in (body.get("messages") or [])],
            "blocks": [[b.get("type") if isinstance(b, dict) else "str"
                        for b in (m.get("content") if isinstance(m.get("content"), list)
                                  else [m.get("content")])]
                       for m in (body.get("messages") or [])],
            "system_blocks": len(body.get("system") or []),
            "tools": len(body.get("tools") or []),
            "stream": bool(body.get("stream")),
            # WHAT WAS SENT, not merely how much of it. Shapes alone cannot answer whether
            # the prompt that reached the model is the prompt whose digest a preflight
            # froze -- and computing digests in a preflight establishes nothing about the
            # outbound request unless something observes the outbound request.
            #
            # Digests rather than bodies: a digest is what the registration froze, it is
            # what a comparison needs, and it keeps a log of full prompts out of the tree.
            # The prefix is for reading a failure, and is short enough not to be the prompt.
            "user_text_digests": _text_digests(body, "user"),
            "first_user_prefix": _first_text(body, "user")[:120],
            "system_text_digests": _text_digests(body, None, system=True),
        })
        if not scripted:
            # A request with no client-side tool definitions is not a loop request. It is
            # also the shape of the UPSTREAM CONTROL a rehearsal sends before launching
            # anything: the instance token comes back through the forwarder, so the caller
            # can prove the port it is about to spend against reaches THIS stub and not a
            # forwarder pointed at a paid endpoint. The log cannot serve that purpose --
            # it is only written on SIGTERM, so mid-run it does not exist.
            answer = Handler.instance_token or "untitled"
            payload = stream([{"text": answer}], body.get("model") or "stub")
            return self._sse(payload)
        i = Handler.served
        Handler.served += 1
        if i >= len(Handler.script):
            # The script is exhausted: end the loop rather than looping forever.
            payload = stream([{"text": "script exhausted"}], body.get("model") or "stub")
        else:
            payload = stream(Handler.script[i], body.get("model") or "stub")
        self._sse(payload)

    def _sse(self, payload: bytes):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()

    def _json(self, obj: dict):
        msg = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8903)
    ap.add_argument("--script", default="sequential", choices=sorted(SCRIPTS))
    ap.add_argument("--log", default="")
    ap.add_argument("--instance-token", default="",
                    help="echoed to non-loop requests, so a caller can prove which stub "
                         "instance a forwarder actually reaches")
    args = ap.parse_args(argv[1:])
    Handler.script = SCRIPTS[args.script]
    Handler.instance_token = args.instance_token
    srv = Server(("127.0.0.1", args.port), Handler)

    def dump(*_a):
        """The driver stops this process with SIGTERM, so the log is written from the
        handler as well -- a `finally` alone leaves `served: null` and the request count,
        which is the point of the stub, unobserved."""
        if args.log:
            with open(args.log, "w") as fh:
                json.dump({"script": args.script,
                           "scripted_turns_served": Handler.served,
                           "requests_received": len(Handler.requests),
                           "requests": Handler.requests}, fh, indent=1)
        srv._BaseServer__shutdown_request = True

    signal.signal(signal.SIGTERM, dump)
    signal.signal(signal.SIGINT, dump)
    print(f"stub_turns: script={args.script} turns={len(Handler.script)} "
          f"on 127.0.0.1:{args.port}", flush=True)
    try:
        srv.serve_forever()
    finally:
        dump()
        print(f"stub_turns: {len(Handler.requests)} request(s), "
              f"{Handler.served} scripted -> {args.log}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
