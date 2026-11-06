from __future__ import annotations

import http.client
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from nexus_memory.atlas.binding import open_binding
from nexus_memory.atlas.server import AtlasServer
from nexus_memory.domain.models import MemoryInput

HOSTILE = "<script>window.pwned=1</script><img src=x onerror=window.pwned=1>"


class Client:
    def __init__(self, server: AtlasServer) -> None:
        self.server = server
        self.cookie: str | None = None

    def request(self, method, path, *, host=None, origin=None, body=None, cookie=True, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        send = {"Host": host or f"127.0.0.1:{self.server.port}", **(headers or {})}
        if origin:
            send["Origin"] = origin
        if cookie and self.cookie:
            send["Cookie"] = self.cookie
        payload = json.dumps(body) if body is not None else None
        if payload:
            send["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=send)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict((k.lower(), v) for k, v in response.getheaders()), data)
        connection.close()
        return result

    def sign_in(self):
        status, headers, _ = self.request("POST", "/session", body={"key": self.server.launch_secret},
                                          origin=f"http://127.0.0.1:{self.server.port}", cookie=False)
        assert status == 204, status
        self.cookie = headers["set-cookie"].split(";")[0]
        return headers["set-cookie"]

    def get_json(self, path, **kw):
        status, headers, data = self.request("GET", path, **kw)
        return status, (json.loads(data) if data and headers.get("content-type", "").startswith("application/json") else data)


@contextmanager
def running(enrolled):
    binding = open_binding(enrolled.checkout, enrolled.resolve)
    server = AtlasServer(binding)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture
def atlas(enrolled):
    ids = {"hostile": enrolled.record(HOSTILE, "k-h", kind="observation", tags=["x"])}
    ids["plain"] = enrolled.record("Caption retention is 72 hours", "k-p", kind="decision", tags=["queue"])
    with running(enrolled) as server:
        client = Client(server)
        client.ids = ids
        client.enrolled = enrolled
        yield client


def test_secret_is_exchanged_once_for_a_hardened_cookie(atlas) -> None:
    cookie = atlas.sign_in()
    assert re.fullmatch(r"atlas_[0-9a-f]{8}=[\w-]{32,}; HttpOnly; Path=/; SameSite=Strict", cookie), cookie
    status, _, _ = atlas.request("POST", "/session", body={"key": atlas.server.launch_secret},
                                 origin=f"http://127.0.0.1:{atlas.server.port}", cookie=False)
    assert status == 403                     # one use only


def test_wrong_secret_malformed_and_foreign_origin_are_refused(atlas) -> None:
    origin = f"http://127.0.0.1:{atlas.server.port}"
    assert atlas.request("POST", "/session", body={"key": "nope"}, origin=origin, cookie=False)[0] == 403
    assert atlas.request("POST", "/session", body={"key": 1}, origin=origin, cookie=False)[0] == 400
    assert atlas.request("POST", "/session", body={"key": atlas.server.launch_secret},
                         origin="http://evil.example", cookie=False)[0] == 403
    assert atlas.request("POST", "/session", body={"key": atlas.server.launch_secret}, cookie=False)[0] == 403  # no Origin
    atlas.sign_in()                          # the real secret still works after those refusals


def test_data_needs_a_session_and_unauthenticated_responses_hold_no_project_text(atlas) -> None:
    for path in ("/api/overview", "/api/search?q=caption", f"/api/inspect?id={atlas.ids['plain']}"):
        status, headers, data = atlas.request("GET", path)
        assert status == 401 and b"Caption" not in data and b"72 hours" not in data
    for asset in ("/", "/app.js", "/atlas.css", "/graph.js"):
        status, _, data = atlas.request("GET", asset)
        assert status == 200 and b"72 hours" not in data and atlas.server.launch_secret.encode() not in data


def test_wrong_host_and_foreign_origin_are_refused_even_with_a_valid_cookie(atlas) -> None:
    atlas.sign_in()
    assert atlas.get_json("/api/overview")[0] == 200
    assert atlas.get_json("/api/overview", host="attacker.example")[0] == 403
    assert atlas.get_json("/api/overview", host=f"attacker.example:{atlas.server.port}")[0] == 403
    assert atlas.get_json("/api/overview", host="127.0.0.1")[0] == 403          # port missing
    assert atlas.get_json("/api/overview", host=f"localhost:{atlas.server.port}")[0] == 200
    assert atlas.get_json("/api/overview", origin="http://evil.example")[0] == 403
    assert atlas.get_json("/api/overview", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403


def test_only_get_and_the_session_post_exist(atlas) -> None:
    atlas.sign_in()
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        assert atlas.request(method, "/api/overview")[0] in (405, 501)
    origin = f"http://127.0.0.1:{atlas.server.port}"
    assert atlas.request("POST", "/api/overview", body={}, origin=origin)[0] == 405
    assert atlas.request("POST", "/api/record", body={"content": "x"}, origin=origin)[0] in (404, 405)


@pytest.mark.parametrize("path", ["/../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/static/../app.js",
                                  "/app.js/..", "//etc/passwd", "/atlas.py", "/binding.py", "/%00"])
def test_static_paths_are_an_allowlist(atlas, path) -> None:
    status, _, data = atlas.request("GET", path)
    assert status == 404 and b"root:" not in data


def test_browser_cannot_supply_scope_paths_or_unknown_parameters(atlas) -> None:
    atlas.sign_in()
    for query in ("namespace=other", "actor=x", "db=/etc/passwd", "path=../../x&evil=1", "limit=0", "limit=1000",
                  "kind=bogus", "cursor=" + "x" * 5000):
        assert atlas.get_json("/api/search?" + query)[0] == 400, query


def test_every_route_returns_scoped_text_only_and_headers_are_hardened(atlas) -> None:
    atlas.sign_in()
    plain = atlas.ids["plain"]
    status, headers, data = atlas.request("GET", "/api/overview")
    assert status == 200
    assert headers["cache-control"] == "no-store" and headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY" and headers["x-content-type-options"] == "nosniff"
    csp = headers["content-security-policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "frame-ancestors 'none'" in csp
    status, found = atlas.get_json("/api/search?q=caption")
    assert status == 200 and [i["memory_id"] for i in found["items"]] == [plain]
    assert found["matched"] == 1 and found["total"] >= 2
    status, view = atlas.get_json(f"/api/inspect?id={plain}")
    assert status == 200 and view["memory_id"] == plain and "content" not in view
    rev = view["revision_id"]
    assert atlas.get_json(f"/api/body?id={plain}&rev={rev}")[1]["content"] == "Caption retention is 72 hours"
    assert atlas.get_json(f"/api/history?id={plain}")[0] == 200
    assert atlas.get_json(f"/api/compare?id={plain}&rev={rev}")[1]["state"] == "root"
    assert atlas.get_json(f"/api/neighbors?id={plain}")[0] == 200
    assert atlas.get_json(f"/api/related?id={plain}&tag=queue")[0] == 200
    assert atlas.get_json("/api/citations?repo=r&path=a.py&commit=" + "a" * 40)[0] == 200


def test_hostile_stored_text_is_returned_only_as_json_text(atlas) -> None:
    atlas.sign_in()
    hostile = atlas.ids["hostile"]
    for path in (f"/api/body?id={hostile}&rev={atlas.get_json(f'/api/inspect?id={hostile}')[1]['revision_id']}",
                 "/api/search?q=script", f"/api/inspect?id={hostile}"):
        status, headers, data = atlas.request("GET", path)
        assert status == 200 and headers["content-type"].startswith("application/json")
        assert headers["x-content-type-options"] == "nosniff"
        assert b"<script" not in data and b"<img" not in data          # escaped even inside JSON strings
        assert json.loads(data)                                         # and still the same text once parsed
    for asset in ("/", "/app.js"):
        _, _, data = atlas.request("GET", asset)
        assert b"innerHTML" not in data and b"eval(" not in data and b"document.write" not in data


def test_missing_and_forgotten_identities_are_404_and_bad_ids_are_400(atlas) -> None:
    atlas.sign_in()
    assert atlas.get_json("/api/inspect?id=does-not-exist")[0] == 404
    assert atlas.get_json("/api/inspect?id=" + "a" * 500)[0] == 400
    assert atlas.get_json("/api/inspect")[0] == 400
    service = atlas.enrolled.writer()
    gone = atlas.enrolled.record("Soon forgotten", "k-gone", kind="observation")
    rev = atlas.get_json(f"/api/inspect?id={gone}")[1]["revision_id"]
    service.forget(gone, rev, "k-forget")
    assert atlas.get_json(f"/api/inspect?id={gone}")[0] == 404
    assert atlas.get_json("/api/search?q=forgotten")[1]["items"] == []


def test_expired_search_cursor_restarts_and_says_so(atlas) -> None:
    atlas.sign_in()
    for n in range(4):
        atlas.enrolled.record(f"Cursor sample {n} retention", f"k-c{n}", kind="observation")
    first = atlas.get_json("/api/search?q=retention&limit=2")[1]
    assert first["cursor"]
    atlas.enrolled.record("Another write", "k-w", kind="observation")
    status, again = atlas.get_json("/api/search?q=retention&limit=2&cursor=" + first["cursor"])
    assert status == 200 and again["restarted"] is True


def test_binding_failure_stops_all_data_and_stays_stopped(atlas) -> None:
    atlas.sign_in()
    assert atlas.get_json("/api/overview")[0] == 200
    os.replace(atlas.enrolled.database, atlas.enrolled.database.with_suffix(".moved"))
    assert atlas.get_json("/api/overview")[0] == 503
    os.replace(atlas.enrolled.database.with_suffix(".moved"), atlas.enrolled.database)    # even if it comes back
    status, body = atlas.get_json("/api/overview")
    assert status == 503 and b"Caption" not in json.dumps(body).encode()


def test_logs_never_contain_the_secret_terms_cursors_or_paths(atlas, capfd) -> None:
    atlas.sign_in()
    atlas.get_json("/api/search?q=caption+private-term&limit=1")
    atlas.get_json(f"/api/inspect?id={atlas.ids['plain']}")
    out = capfd.readouterr()
    text = out.out + out.err
    for forbidden in (atlas.server.launch_secret, "private-term", atlas.ids["plain"], "caption", "cursor"):
        assert forbidden not in text


def test_launch_file_carries_the_secret_in_a_fragment_and_is_private(enrolled, tmp_path) -> None:
    binding = open_binding(enrolled.checkout, enrolled.resolve)
    server = AtlasServer(binding)
    try:
        path = server.write_launch_file(tmp_path / "launch")
        assert (path.stat().st_mode & 0o777) == 0o600 and (path.parent.stat().st_mode & 0o777) == 0o700
        text = path.read_text()
        assert f"http://127.0.0.1:{server.port}/#k={server.launch_secret}" in text
        assert "?k=" not in text
        assert server.launch_secret not in server.public_address
    finally:
        server.server_close()


def _assert_hardened(label: str, headers: dict) -> None:
    assert headers.get("cache-control") == "no-store", label
    assert headers.get("referrer-policy") == "no-referrer", label
    assert headers.get("x-frame-options") == "DENY", label
    assert headers.get("x-content-type-options") == "nosniff", label
    policy = dict(part.strip().split(" ", 1) for part in headers.get("content-security-policy", "").split(";") if part.strip())
    assert policy.get("default-src") == "'none'" and policy.get("script-src") == "'self'", label
    assert policy.get("frame-ancestors") == "'none'" and policy.get("base-uri") == "'none'", label
    assert "unsafe-inline" not in headers["content-security-policy"] and "unsafe-eval" not in headers["content-security-policy"], label


def test_every_response_carries_every_hardening_header(atlas) -> None:
    origin = f"http://127.0.0.1:{atlas.server.port}"
    plain = atlas.ids["plain"]
    seen: dict[str, int] = {}

    def check(label: str, response, expected: int) -> None:
        status, headers, _ = response
        assert status == expected, (label, status)
        _assert_hardened(label, headers)
        seen[label] = status

    for path in ("/", "/index.html", "/app.js", "/graph.js", "/connections.js", "/atlas.css"):   # static assets
        check(f"static {path}", atlas.request("GET", path), 200)
    check("404 unknown path", atlas.request("GET", "/nope"), 404)
    check("404 traversal", atlas.request("GET", "/../../etc/passwd"), 404)
    check("403 wrong host", atlas.request("GET", "/", host="attacker.example"), 403)
    check("401 no session", atlas.request("GET", "/api/overview"), 401)
    check("403 wrong secret", atlas.request("POST", "/session", body={"key": "nope"}, origin=origin, cookie=False), 403)
    check("403 no origin", atlas.request("POST", "/session", body={"key": "nope"}, cookie=False), 403)
    check("400 malformed session", atlas.request("POST", "/session", body={"key": 1}, origin=origin, cookie=False), 400)
    check("405 wrong post path", atlas.request("POST", "/api/overview", body={}, origin=origin), 405)
    check("405 method", atlas.request("PUT", "/api/overview"), 405)
    signed = atlas.request("POST", "/session", body={"key": atlas.server.launch_secret}, origin=origin, cookie=False)
    check("204 session POST", signed, 204)
    atlas.cookie = signed[1]["set-cookie"].split(";")[0]
    check("403 session secret reused", atlas.request("POST", "/session", body={"key": atlas.server.launch_secret},
                                                      origin=origin, cookie=False), 403)
    check("200 overview", atlas.request("GET", "/api/overview"), 200)
    check("200 search", atlas.request("GET", "/api/search?q=caption"), 200)
    check("200 inspect", atlas.request("GET", f"/api/inspect?id={plain}"), 200)
    check("200 hostile body", atlas.request("GET", f"/api/inspect?id={atlas.ids['hostile']}"), 200)
    check("404 invisible identity", atlas.request("GET", "/api/inspect?id=does-not-exist"), 404)
    check("404 unknown api route", atlas.request("GET", "/api/nope"), 404)
    check("400 unknown parameter", atlas.request("GET", "/api/search?namespace=other"), 400)
    check("403 cross-site", atlas.request("GET", "/api/overview", headers={"Sec-Fetch-Site": "cross-site"}), 403)
    os.replace(atlas.enrolled.database, atlas.enrolled.database.with_suffix(".moved"))
    check("503 binding lost", atlas.request("GET", "/api/overview"), 503)
    # 409 is not listed: an expired search cursor restarts the list with a notice (200), by design.
    assert len(seen) >= 20
