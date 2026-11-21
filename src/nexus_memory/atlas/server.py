"""Loopback HTTP server for the Atlas viewer.

Read-only and local: it binds 127.0.0.1 on an ephemeral port, accepts a per-launch secret
once, and answers a fixed set of GET routes from ``AtlasQueries``. The secret never appears
in a URL path or query, in a log, or in a process argument list.
"""
from __future__ import annotations

import dataclasses
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs

from nexus_memory.atlas.binding import AtlasBinding, BindingUnavailable
from nexus_memory.atlas.graph_queries import ScopeLost
from nexus_memory.atlas.owner import ApprovalInvalid
from nexus_memory.atlas.queries import AtlasQueries
from nexus_memory.domain.errors import (
    FlowDenied, RevisionConflict, IdempotencyConflict, ResolutionConflict, StalePack,
    AuthorityBusy, PublicationBusy, GrantRevoked, SpaceNotMounted, CursorExpired,
    MemoryNotFound,
    NexusError,
    StorageIntegrityError,
    UnsupportedRuntime,
    UnsupportedSchema,
)
from nexus_memory.domain.models import SearchQuery

_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/atlas.css": ("atlas.css", "text/css; charset=utf-8"),
    "/spaces.js": ("spaces.js", "text/javascript; charset=utf-8"),
    "/paths.js": ("paths.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
    "/connections.js": ("connections.js", "text/javascript; charset=utf-8"),
}
_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_FATAL = (BindingUnavailable, UnsupportedSchema, UnsupportedRuntime)


class _Bad(Exception):
    pass


def _json_bytes(value) -> bytes:
    text = json.dumps(value, default=lambda o: dataclasses.asdict(o) if dataclasses.is_dataclass(o) else str(o),
                      separators=(",", ":"))
    # Valid JSON that no HTML parser can mistake for markup, as a second line of defence.
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").encode("ascii", "backslashreplace")


class AtlasServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, binding: AtlasBinding | None = None, *, graph=None, owner=None) -> None:
        if (binding is None) == (graph is None) or owner and graph is None:
            raise ValueError('choose exactly one Atlas binding')
        if graph and not owner and len(graph.mounts) > 1:
            raise ValueError('Stage A viewer requires a single mount')
        if owner and any(b.catalog.path != owner.catalog.path for b in graph.mounts.values()):
            raise ValueError('owner catalog mismatch')
        self.graph, self.owner = graph, owner
        self.owner_key = secrets.token_urlsafe(32) if owner else None
        self.owner_session = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", 0), _Handler)
        self.port = self.server_address[1]
        self.queries = AtlasQueries(binding) if binding else None
        self.launch_secret = secrets.token_urlsafe(32)
        self.public_address = f"http://127.0.0.1:{self.port}/"
        self._secret_used = False
        self._lock = threading.Lock()
        self._cookie_name = "atlas_" + secrets.token_hex(4)
        self._session = secrets.token_urlsafe(32)
        self.failed = False
        self._launch_file: Path | None = None
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    @property
    def origins(self) -> set[str]:
        return {f"http://{host}" for host in self.hosts}

    def write_launch_file(self, directory: Path, *, owner=False) -> Path:
        """A private page that sends the browser to the viewer with the secret in the fragment.

        Opening this file puts only its path on the opener's command line, and a fragment is
        never sent to the server, written to a log or put in a Referer.
        """
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        path = directory / "launch.html"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            secret = self.owner_key if owner else self.launch_secret
            if secret is None:
                raise ValueError('owner launch unavailable')
            url = f"http://127.0.0.1:{self.port}/#k={secret}"
            handle.write(f'<!doctype html><meta charset="utf-8"><title>Memory Atlas</title>'
                         f'<script>location.replace("{url}")</script><a href="{url}">Open Memory Atlas</a>')
        self._launch_file = path
        return path

    def exchange(self, key: str) -> str | None:
        with self._lock:
            if self.owner_key and hmac.compare_digest(key.encode(), self.owner_key.encode()):
                from time import time
                if time() >= self.owner.expires:
                    return None
                self.owner_key = None
                session = self.owner_session
            else:
                if self._secret_used or not hmac.compare_digest(key.encode(), self.launch_secret.encode()):
                    return None
                self._secret_used = True
                session = self._session
        if self._launch_file is not None:
            self._launch_file.unlink(missing_ok=True)
        return f"{self._cookie_name}={session}; HttpOnly; Path=/; SameSite=Strict"

    def has_session(self, cookie_header: str | None, *, owner=False) -> bool:
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self._cookie_name and hmac.compare_digest(value.encode(), (self.owner_session if owner else self._session).encode()):
                return True
        return False


class _Handler(BaseHTTPRequestHandler):
    server: AtlasServer
    server_version = "Atlas"
    sys_version = ""

    def log_message(self, *args) -> None:   # the default writes the request line, which holds search terms
        pass

    def _send(self, status: int, body: bytes = b"", content_type: str = "application/json; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", _CSP)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str) -> None:
        self._send(status, _json_bytes({"error": code}))

    def _host_ok(self) -> bool:
        return len(self.headers.get_all("Host", [])) == 1 and self.headers["Host"] in self.server.hosts

    def _same_origin(self, required: bool) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return not required
        return self.headers.get_all('Origin') == ['http://' + self.headers['Host']]

    def do_GET(self) -> None:   # noqa: N802
        if not self._host_ok():
            return self._error(403, "forbidden")
        route = self.path.split("?", 1)[0]
        if route in _STATIC:
            name, content_type = _STATIC[route]
            return self._send(200, files("nexus_memory.atlas.static").joinpath(name).read_bytes(), content_type)
        if not route.startswith("/api/"):
            return self._error(404, "not_found")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if not self._same_origin(False) or fetch_site not in (None, "same-origin", "none"):
            return self._error(403, "forbidden")
        owner = self._owner_session()
        if not owner and not self.server.has_session(self.headers.get("Cookie")):
            return self._error(401, "scope_lost" if self.server.graph else "unauthorized")
        if self.server.failed:
            return self._error(503, "binding_unavailable")
        if self.server.graph and (not self.server.owner or owner):
            return self._scoped_get(route,owner)
        if self.server.graph:
            return self._error(403, "forbidden")
        handler = _ROUTES.get(route)
        if handler is None:
            return self._error(404, "not_found")
        try:
            query = self.path.partition("?")[2]
            params = parse_qs(query, keep_blank_values=True, strict_parsing=bool(query), max_num_fields=16)
            result = handler(self.server.queries, params)
        except _Bad:
            return self._error(400, "invalid_request")
        except MemoryNotFound:
            return self._error(404, "not_found")
        except CursorExpired:
            return self._error(409, "cursor_expired")
        except _FATAL:
            self.server.failed = True            # stop serving data; a returning store does not undo it
            return self._error(503, "binding_unavailable")
        except StorageIntegrityError:
            return self._error(503, "storage_unavailable")
        except (NexusError, ValueError):
            return self._error(400, "invalid_request")
        except Exception:   # noqa: BLE001 - nothing about the failure is shown to the client
            return self._error(500, "internal_error")
        self._send(200, _json_bytes(result))

    def _owner_session(self):
        from time import time
        return (self.server.owner and time() < self.server.owner.expires and
                self.server.has_session(self.headers.get('Cookie'),owner=True))

    def _scoped_get(self, route, owner):
        graph = self.server.graph
        try:
            if self.path == '/api/v2/spaces':
                return self._send(200,_json_bytes({'spaces':graph.spaces(),'csrf':self.server.csrf if owner else None}))
            if route.startswith('/api/v2/owner/'):
                return self._error(405,'method_not_allowed')
            if route == '/api/v2/proposals': return self._error(501,'unsupported_capability')
            params = _take(parse_qs(self.path.partition('?')[2],strict_parsing=True,max_num_fields=1),{'space'})
            sid = _identifier(params,'space')
            parts = route.split('/')
            if parts[:3] != ['', 'api','v2'] or not all(_IDENTIFIER.fullmatch(p) for p in parts[3:]): raise KeyError
            if len(parts)==5 and parts[3]=='packs': value = graph.pack(sid,parts[4])
            elif len(parts)==7 and parts[3]=='assertions' and parts[5]=='revisions':
                value = graph.assertion(sid,parts[4],parts[6])
            else: raise KeyError
            self._send(200,_json_bytes(value))
        except KeyError: self._error(404,'not_found')
        except StalePack: self._error(409,'stale_pack')
        except (ScopeLost, GrantRevoked, SpaceNotMounted): self._error(403,'scope_lost')
        except (_Bad, ValueError): self._error(400,'invalid_request')
        except Exception: self._error(503,'request_unavailable')

    def _owner_post(self, action):
        if not (self.headers.get_all('Origin') == ['http://'+self.headers['Host']] and
                self.headers.get_all('X-Atlas-CSRF') == [self.server.csrf]):
            return self._error(403,'forbidden')
        if not self._owner_session():
            return self._error(403,'scope_lost')
        try:
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[])) != 1:
                raise _Bad
            length = int(self.headers['Content-Length'])
            if length > 1 << 20: return self._error(413,'body_too_large')
            if length <= 0 or self.headers.get_content_type() != 'application/json': raise _Bad
            body = json.loads(self.rfile.read(length))
            if action == 'review': return self._error(501,'unsupported_capability')
            # Stage A grants here are short-lived publication approvals, never a
            # browser-selected grant file, store locator or receiving context.
            if action == 'grants': action = 'preview'
            if action not in ('curate','correct','preview'): return self._error(404,'not_found')
            broker = self.server.graph.mounts.get(body['space'])
            if broker is None: raise ScopeLost
            value = self.server.owner.command(action,body,grant=broker.grant)
            self._send(200,_json_bytes(value))
        except (RevisionConflict, IdempotencyConflict, ResolutionConflict) as exc: self._error(409,exc.code)
        except (ScopeLost, GrantRevoked, SpaceNotMounted): self._error(403,'scope_lost')
        except ApprovalInvalid: self._error(403,'approval_invalid')
        except (AuthorityBusy, PublicationBusy): self._error(503,'request_unavailable')
        except FlowDenied: self._error(403,'forbidden')
        except (ValueError, KeyError, TypeError, NexusError, _Bad): self._error(400,'invalid_request')
        except Exception: self._error(500,'internal_error')

    def do_POST(self) -> None:   # noqa: N802
        if not self._host_ok():
            return self._error(403, "forbidden")
        if self.path.startswith('/api/v2/owner/'):
            return self._owner_post(self.path.removeprefix('/api/v2/owner/'))
        if self.path != "/session":
            return self._error(405, "method_not_allowed")
        if not self._same_origin(True):
            return self._error(403, "forbidden")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 1024:
                raise ValueError
            key = json.loads(self.rfile.read(length))["key"]
            if not isinstance(key, str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._error(400, "invalid_request")
        cookie = self.server.exchange(key)
        if cookie is None:
            return self._error(403, "forbidden")
        self._send(204, b"", extra={"Set-Cookie": cookie})

    def _refuse(self) -> None:
        self._error(405, "method_not_allowed")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _refuse   # noqa: N815


# ---- routes -----------------------------------------------------------------------------

def _take(params: dict, allowed: set[str], many: set[str] = frozenset()) -> dict:
    if set(params) - allowed or any(len(v) != 1 for k, v in params.items() if k not in many):
        raise _Bad
    return {k: (v if k in many else v[0]) for k, v in params.items()}


def _identifier(params: dict, key: str, required: bool = True) -> str | None:
    value = params.get(key)
    if value is None:
        if required:
            raise _Bad
        return None
    if not _IDENTIFIER.fullmatch(value):
        raise _Bad
    return value


def _limit(params: dict, default: int) -> int:
    try:
        value = int(params.get("limit", default))
    except ValueError as error:
        raise _Bad from error
    if not 1 <= value <= 100:
        raise _Bad
    return value


def _search_query(params: dict) -> SearchQuery:
    return SearchQuery(
        query=params.get("q") or None, kinds=tuple(params.get("kind", ())), tags_all=tuple(params.get("tag", ())),
        limit=_limit(params, 20), cursor=params.get("cursor") or None,
        reference_paths=(params["path"],) if "path" in params else (),
        reference_path_prefix=params.get("prefix") or None,
        reference_commits=(params["commit"],) if "commit" in params else ())


def _search(q: AtlasQueries, raw: dict):
    p = _take(raw, {"q", "kind", "tag", "limit", "cursor", "path", "prefix", "commit", "repo"}, {"kind", "tag"})
    return q.search(_search_query(p), _identifier(p, "repo", False))


def _citations(q: AtlasQueries, raw: dict):
    p = _take(raw, {"q", "kind", "tag", "limit", "cursor", "repo", "path", "commit"}, {"kind", "tag"})
    if not {"repo", "path", "commit"} <= set(p):
        raise _Bad
    base = _search_query({k: v for k, v in p.items() if k in {"q", "kind", "tag", "limit", "cursor"}})
    return q.citations(_identifier(p, "repo"), p["path"], p["commit"], base)


def _inspect(q: AtlasQueries, raw: dict):
    p = _take(raw, {"id", "rev"})
    return q.inspect(_identifier(p, "id"), _identifier(p, "rev", False))


def _body(q: AtlasQueries, raw: dict):
    p = _take(raw, {"id", "rev"})
    return {"content": q.body(_identifier(p, "id"), _identifier(p, "rev"))}


def _history(q: AtlasQueries, raw: dict):
    p = _take(raw, {"id", "cursor", "limit"})
    return q.history(_identifier(p, "id"), p.get("cursor") or None, _limit(p, 20))


def _compare(q: AtlasQueries, raw: dict):
    p = _take(raw, {"id", "rev"})
    return q.compare(_identifier(p, "id"), _identifier(p, "rev"))


def _neighbors(q: AtlasQueries, raw: dict):
    p = _take(raw, {"id", "rev", "limit"})
    return q.neighbors(_identifier(p, "id"), _identifier(p, "rev", False), _limit(p, 25))


def _related(q: AtlasQueries, raw: dict):
    p = _take(raw, {"id", "tag", "term", "limit", "cursor"})
    return q.related(_identifier(p, "id"), tag=p.get("tag"), term=p.get("term"),
                     limit=_limit(p, 20), cursor=p.get("cursor") or None)


_ROUTES = {
    "/api/overview": lambda q, raw: (_take(raw, set()), q.overview())[1],
    "/api/search": _search, "/api/citations": _citations, "/api/inspect": _inspect, "/api/body": _body,
    "/api/history": _history, "/api/compare": _compare, "/api/neighbors": _neighbors, "/api/related": _related,
}


def serve(binding: AtlasBinding | None = None, open_browser: bool = True, *, graph=None, owner=None) -> None:
    """Serve a legacy binding or launcher-bound graph. Owner access is explicit."""
    server = AtlasServer(binding, graph=graph, owner=owner)
    directory = Path(tempfile.mkdtemp(prefix="nexus-atlas-"))
    launch = server.write_launch_file(directory, owner=owner is not None)
    print(f"Memory Atlas is running at {server.public_address}", flush=True)
    opened = open_browser and webbrowser.open(launch.as_uri())
    if not opened:
        print(f"Open this private file in a browser to sign in: {launch}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        launch.unlink(missing_ok=True)
        directory.rmdir()
