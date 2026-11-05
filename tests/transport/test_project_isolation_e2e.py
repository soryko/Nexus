"""Project isolation over real stdio MCP processes and the Antigravity hook.

Set NEXUS_E2E_CODEX_SERVER to an installed hook-enabled Nexus executable to
include Codex's native recall. NEXUS_E2E_SERVER can select the plain server.
NEXUS_E2E_INTEGRATION_DIR can select a deployed copy of the launcher and hook.
NEXUS_E2E_AGY_PLUGIN selects the installed plugin manifests for a registered-command smoke.
Each test gives the host processes a new private home and disposable Git repos.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters, types


ROOT = Path(__file__).resolve().parents[2]
INTEGRATION = Path(os.environ.get("NEXUS_E2E_INTEGRATION_DIR", ROOT / "integrations/project_scope"))
LAUNCHER = INTEGRATION / "launcher.py"
HOOK = INTEGRATION / "antigravity_hook.py"
RETIRE = ROOT / "tools/retire_legacy_store.py"
SERVER = Path(os.environ.get("NEXUS_E2E_SERVER", Path(sys.executable).with_name("nexus-memory")))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "proof.txt").write_text(path.parent.name)
    _git(path, "add", "proof.txt")
    _git(path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "fixture reference")
    return path


def _env(home: Path, cwd: Path) -> dict[str, str]:
    return {"PATH": os.environ["PATH"], "HOME": str(home), "PWD": str(cwd),
            "XDG_DATA_HOME": str(home / ".local/share"), "GIT_CONFIG_NOSYSTEM": "1"}


def _enroll(home: Path, repo: Path) -> None:
    subprocess.run([sys.executable, "-I", LAUNCHER, "enroll", repo], cwd=repo,
                   env=_env(home, repo), check=True, capture_output=True)


def _client(home: Path, repo: Path, role: str, server: Path, *extra: str) -> Client:
    return Client(StdioServerParameters(
        command=sys.executable, args=["-I", str(LAUNCHER), role, str(server), *extra],
        cwd=repo, env=_env(home, repo)))


def _hook(home: Path, cwd: Path, workspaces: list[Path], term: str) -> dict:
    payload = {"lastUserInput": f"Find {term}", "workspacePaths": list(map(str, workspaces))}
    result = subprocess.run([SERVER.with_name("python"), "-I", HOOK, "recall"], cwd=cwd, env=_env(home, cwd),
                            input=json.dumps(payload), text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


async def _call(client: Client, tool: str, arguments: dict) -> dict:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    return result.structured_content


async def _assert_inaccessible(client: Client, receipt: dict, term: str) -> None:
    mid, rid = receipt["memory_id"], receipt["revision_id"]
    assert not (await _call(client, "search", {"query": term}))["hits"]
    # Known IDs and a correct expected revision must not bypass isolation.
    for tool, arguments in (
        ("get", {}),
        ("get", {"revision_id": rid}),
        ("history", {}),
        ("revise", {"expected_revision_id": rid, "content": "forbidden", "idempotency_key": "alter-" + mid}),
        ("forget", {"expected_revision_id": rid, "idempotency_key": "delete-" + mid}),
    ):
        result = await client.call_tool(tool, {"memory_id": mid, **arguments})
        assert result.is_error and result.content[0].text.startswith("not_found:"), (tool, result.content)


@pytest.mark.parametrize("role", ["antigravity", "claude", "codex"])
def test_project_readers_exclude_legacy_and_other_projects(tmp_path: Path, role: str) -> None:
    reader_server = SERVER
    if role == "codex":
        if "NEXUS_E2E_CODEX_SERVER" not in os.environ:
            pytest.skip("set NEXUS_E2E_CODEX_SERVER to the installed host adapter")
        reader_server = Path(os.environ["NEXUS_E2E_CODEX_SERVER"])
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    a, b = _repo(tmp_path / "a/api"), _repo(tmp_path / "b/api")
    linked = tmp_path / "another-name"
    _git(a, "worktree", "add", "--detach", str(linked))
    old_db = home / "Library/Application Support/Nexus Memory/memory.sqlite3"
    old_db.parent.mkdir(parents=True)
    legacy = StdioServerParameters(command=str(SERVER), args=["--db", str(old_db), "--namespace", "api",
                                  "--actor", "local", "--repo", str(b)], cwd=b, env=_env(home, b))

    async def scenario() -> None:
        # Create real legacy data before the project registry exists. A verified
        # Git reference is evidence, not permission to show the row to a reader.
        async with Client(legacy) as old:
            plain = await _call(old, "record", {"content": "oldplain", "idempotency_key": "plain"})
            proof = await _call(old, "record", {"content": "oldproof", "idempotency_key": "proof",
                                                "references": [{"path": "proof.txt"}]})
            assert (await _call(old, "get", {"memory_id": proof["memory_id"]}))["references"]
            _enroll(home, a)
            _enroll(home, b)
            async with _client(home, b, "claude", SERVER) as writer_b:
                foreign = await _call(writer_b, "record", {"content": "foreignzircon", "idempotency_key": "key"})
            async with _client(home, a, "antigravity", SERVER) as writer_a:
                own = await _call(writer_a, "record", {"content": "localcobalt", "idempotency_key": "key"})

            expected = ("--expected-project", str(a)) if role == "codex" else ()
            async with _client(home, linked, role, reader_server, *expected) as reader:
                assert (await _call(reader, "get", {"memory_id": own["memory_id"]}))["content"] == "localcobalt"
                assert [hit["memory_id"] for hit in (await _call(reader, "search", {}))["hits"]] == [own["memory_id"]]
                for receipt, term in ((foreign, "foreignzircon"), (plain, "oldplain"), (proof, "oldproof")):
                    await _assert_inaccessible(reader, receipt, term)
                if role == "codex":
                    for term, expected_mid in (("localcobalt", own["memory_id"]), ("foreignzircon", None),
                                               ("oldplain", None), ("oldproof", None)):
                        recall = await _call(reader, "codex_recall", {"cwd": str(linked), "session_id": term,
                                                                     "prompt": f"find {term}"})
                        if expected_mid:
                            assert expected_mid in json.dumps(recall)
                        else:
                            assert not recall
            # Failed foreign mutations must leave both legacy rows intact.
            for receipt, term in ((plain, "oldplain"), (proof, "oldproof")):
                assert (await _call(old, "get", {"memory_id": receipt["memory_id"]}))["content"] == term
            async with _client(home, b, "claude", SERVER) as owner_b:
                stored = await _call(owner_b, "get", {"memory_id": foreign["memory_id"]})
                assert stored["current_revision_id"] == foreign["revision_id"] and stored["content"] == "foreignzircon"

        if role == "antigravity":
            positive = json.dumps(_hook(home, linked, [linked], "localcobalt")["injectSteps"])
            assert own["memory_id"] in positive
            plugin = home / ".gemini/config/plugins/nexus-memory"
            plugin.mkdir(parents=True)
            assert own["memory_id"] in json.dumps(_hook(home, plugin, [a], "localcobalt")["injectSteps"])
            for term in ("foreignzircon", "oldplain", "oldproof"):
                assert not _hook(home, a, [a], term)["injectSteps"]
            assert not _hook(home, b, [a], "localcobalt")["injectSteps"]
            assert not _hook(home, a, [a, b], "localcobalt")["injectSteps"]
            assert not _hook(home, a, [tmp_path / "missing/api"], "localcobalt")["injectSteps"]

    anyio.run(scenario)


def test_foreign_checkout_cannot_impersonate_an_enrolled_project(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    trusted = _repo(tmp_path / "org/api")
    _enroll(home, trusted)
    clone = tmp_path / "clone/api"
    clone.parent.mkdir()
    _git(tmp_path, "clone", str(trusted), str(clone))
    forged = tmp_path / "forged/api"
    forged.mkdir(parents=True)
    (forged / ".git").write_text(f"gitdir: {trusted / '.git'}\n")
    for path in (clone, forged):
        result = subprocess.run([sys.executable, "-I", LAUNCHER, "claude", SERVER], cwd=path,
                                env=_env(home, path), input="", text=True, capture_output=True, timeout=10)
        assert result.returncode != 0, path
        assert not _hook(home, path, [path], "localcobalt")["injectSteps"]


def test_retirement_fences_an_already_running_legacy_server(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = _repo(tmp_path / "legacy/api")
    db = home / "Library/Application Support/Nexus Memory/memory.sqlite3"
    db.parent.mkdir(parents=True)
    archive = tmp_path / "private-recovery"
    archive.mkdir(mode=0o700)
    server = StdioServerParameters(command=str(SERVER), args=["--db", str(db), "--namespace", "api",
                                  "--actor", "local", "--repo", str(repo)], cwd=repo, env=_env(home, repo))
    old_claude = StdioServerParameters(command=str(SERVER), args=["--db", str(db), "--namespace", "nexus-memory",
                                      "--actor", "local"], cwd=repo, env=_env(home, repo))

    async def scenario() -> None:
        async with Client(server) as active, Client(old_claude) as other_partition:
            receipt = await _call(active, "record", {"content": "legacyfencecanary", "idempotency_key": "old-canary",
                                                      "references": [{"path": "proof.txt"}]})
            previous = await _call(other_partition, "record", {"content": "priorword", "idempotency_key": "first"})
            final = await _call(other_partition, "revise", {"content": "lastword", "idempotency_key": "second",
                                "memory_id": previous["memory_id"], "expected_revision_id": previous["revision_id"]})
            key = {"memory_id": receipt["memory_id"]}
            assert (await _call(active, "get", key))["content"] == "legacyfencecanary"
            retirement_command = [sys.executable, RETIRE, "--database", db, "--archive-dir", archive]
            retired = subprocess.run(retirement_command, cwd=repo, env=_env(home, repo), check=True,
                                     capture_output=True, text=True, timeout=30)
            original = Path(retired.stdout.strip())
            assert original.is_file() and original.is_relative_to(archive)
            assert db.is_file()
            for tool, arguments in (("get", key), ("history", key), ("search", {"query": "legacyfencecanary"}),
                                    ("record", {"content": "aftercutover", "idempotency_key": "aftercutover"})):
                response = await active.call_tool(tool, arguments)
                assert response.is_error, (tool, response)
                assert "legacyfencecanary" not in str(response.content)
            for revision in (previous, final):
                result = await other_partition.call_tool("get", {"memory_id": revision["memory_id"],
                                                                 "revision_id": revision["revision_id"]})
                assert result.is_error
            # The owner can recover the original text, ID, and evidence offline.
            with sqlite3.connect(original.as_uri() + "?mode=ro&immutable=1", uri=True) as archived:
                row = archived.execute("""
                    SELECT b.body,r.revision_id FROM revisions r JOIN blobs b ON b.id=r.blob_id
                    WHERE r.namespace=? AND r.actor=? AND r.memory_id=?
                """, ("api", "local", receipt["memory_id"])).fetchone()
                assert row == (b"legacyfencecanary", receipt["revision_id"])
                assert archived.execute("SELECT path FROM revision_references WHERE revision_id=?",
                                        (receipt["revision_id"],)).fetchall() == [("proof.txt",)]
                assert set(archived.execute("SELECT revision_id FROM revisions WHERE namespace='nexus-memory'")) == {
                    (previous["revision_id"],), (final["revision_id"],),
                }
                assert archived.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            again = subprocess.run(retirement_command, cwd=repo, env=_env(home, repo), check=True,
                                   capture_output=True, text=True, timeout=30)
            assert Path(again.stdout.strip()) == original
        # A stale saved command in a *new* host must also fail at the old path.
        stale = subprocess.run([server.command, *server.args], cwd=repo, env=_env(home, repo),
                               input="", text=True, capture_output=True, timeout=10)
        assert stale.returncode != 0
        _enroll(home, repo)
        async with _client(home, repo, "antigravity", SERVER) as fresh:
            await _assert_inaccessible(fresh, receipt, "legacyfencecanary")
            await _call(fresh, "record", {"content": "fresh canary", "idempotency_key": "new"})

    anyio.run(scenario)


def test_reader_opened_during_retirement_cannot_read_original_rows(tmp_path: Path, monkeypatch) -> None:
    """Opening a SQLite handle can beat the first SELECT even during an exclusive lock."""
    db = tmp_path / "legacy.sqlite3"
    server = StdioServerParameters(command=str(SERVER), args=["--db", str(db), "--namespace", "api",
                                  "--actor", "local"], cwd=tmp_path, env=_env(tmp_path, tmp_path))

    async def seed() -> None:
        async with Client(server) as client:
            await _call(client, "record", {"content": "preexchangebody", "idempotency_key": "test"})
    anyio.run(seed)

    spec = importlib.util.spec_from_file_location("retirement_under_test", RETIRE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    copy_archive = module._copy_archive
    # Pause this *separate process* between sqlite3.connect and the operations
    # an old MCP server normally performs immediately afterward on that handle.
    code = """
import sqlite3, sys
with sqlite3.connect(sys.argv[1], isolation_level=None) as conn:
    print("opened", flush=True)
    input()
    conn.execute("PRAGMA synchronous=FULL")
    if conn.execute("SELECT 1 FROM sqlite_schema WHERE name='blobs'").fetchone():
        assert conn.execute("SELECT body FROM blobs").fetchall() == []
"""
    contender = None

    def copy_while_contender_has_fd(*args, **kwargs):
        nonlocal contender
        contender = subprocess.Popen([sys.executable, "-I", "-c", code, str(db)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert contender.stdout.readline().strip() == "opened"
        return copy_archive(*args, **kwargs)

    monkeypatch.setattr(module, "_copy_archive", copy_while_contender_has_fd)
    try:
        module.retire(db, tmp_path / "recovery")
        assert contender is not None
        _, stderr = contender.communicate("\n", timeout=10)
        assert contender.returncode == 0, stderr
    finally:
        if contender is not None and contender.poll() is None:
            contender.kill()
            contender.communicate()


def test_neutral_antigravity_stdio_resolves_each_call_and_rejects_a_workspace_switch(tmp_path: Path) -> None:
    """The host can initialize before a workspace exists and change roots without restarting."""
    home = tmp_path / "home"
    plugin = home / ".gemini/config/plugins/nexus-memory"
    plugin.mkdir(parents=True)
    a = _repo(tmp_path / "org with space/api")
    b = _repo(tmp_path / "other/api")
    _enroll(home, a)
    _enroll(home, b)
    # Execute the real plugin wrapper and retarget only its home-derived paths.
    # All durable data remains under this fixture's home. No configuration is
    # edited in the installation whose entry points we exercise.
    base = home / ".local/share/nexus-memory"
    (base / "project-scopes-v1/bin").symlink_to(INTEGRATION, target_is_directory=True)
    (base / "venvs").mkdir()
    (base / "venvs/0.1.0a2").symlink_to(SERVER.parent.parent, target_is_directory=True)
    registered = os.environ.get("NEXUS_E2E_AGY_PLUGIN")
    package = Path(registered) if registered else ROOT / "integrations/project_scope/antigravity"
    (plugin / "scripts").symlink_to(package / "scripts", target_is_directory=True)
    args = [str(package / "bin/nexus-launcher.py")]
    command = str(SERVER.with_name("python"))
    hook_args = [command, "./scripts/pre_invocation.py"]
    if registered:
        conf = json.loads((package / "mcp_config.json").read_text())["mcpServers"]["nexus-memory"]
        assert conf["command"] == command and conf["args"] == args and not conf.get("env")
        hooks = json.loads((package / "hooks.json").read_text())["nexus-memory-ambient"]
        assert shlex.split(hooks["PreInvocation"][0]["command"]) == hook_args
    # Antigravity starts the configured server from the plugin package with PWD=/.
    # No project path is passed to the command. The host supplies roots via MCP.
    params = StdioServerParameters(command=command, args=args, cwd=plugin, env={**_env(home, plugin), "PWD": "/"})
    roots = []
    requested_roots = 0
    callback_fails = False
    notify_during_reply = None

    async def list_roots(context):
        nonlocal requested_roots
        requested_roots += 1
        if callback_fails:
            return types.ErrorData(code=-32603, message="workspace manager unavailable")
        answer = types.ListRootsResult(roots=[types.Root(uri=uri) for uri in roots])
        if notify_during_reply is not None:
            roots[:] = [b.as_uri()]
            # The installed host negotiates the legacy protocol that includes this notification.
            with pytest.warns(UserWarning, match="roots capability is deprecated"):
                await notify_during_reply.send_roots_list_changed()
        return answer

    def host(server=params, *, with_roots=True):
        return Client(server, mode="legacy", list_roots_callback=list_roots if with_roots else None,
                      read_timeout_seconds=12,
                      client_info=types.Implementation(name="antigravity", version="1.4.12"))

    async def denied(client, name, arguments):
        result = await client.call_tool(name, arguments)
        assert result.is_error, (name, result)
        assert "project_scope_unavailable" in str(result.content), result.content
        assert "scopecanary" not in str(result), "scope failure leaked a stored value"

    async def scenario():
        nonlocal callback_fails, notify_during_reply
        async with _client(home, b, "claude", SERVER) as first_writer:
            other = await _call(first_writer, "record", {"content": "scopecanary from B bluezircon", "idempotency_key": "only-b"})
        scope_dir = home / ".local/share/nexus-memory/project-scopes-v1"
        before = set(scope_dir.glob("*.sqlite3"))
        assert len(before) == 1
        async with host() as client:
            advertised = await client.list_tools()
            assert {t.name for t in advertised.tools} == {"record", "get", "revise", "forget", "history", "search", "status"}
            await denied(client, "record", {"content": "unboundwrite", "idempotency_key": "orphan"})
            assert set(scope_dir.glob("*.sqlite3")) == before
            roots[:] = [a.as_uri()]
            # A first valid call is allowed even if the initial callback was empty.
            lookup_count = requested_roots
            own = await _call(client, "record", {"content": "scopecanary from A greenradar", "idempotency_key": "only-a",
                                                "references": [{"path": "proof.txt"}]})
            assert requested_roots > lookup_count
            fetched = await _call(client, "get", {"memory_id": own["memory_id"]})
            assert fetched["content"] == "scopecanary from A greenradar" and fetched["references"][0]["path"] == "proof.txt"
            prompt = {"workspacePaths": [str(a)], "lastUserInput": "Find greenradar"}
            hook = subprocess.run(hook_args, cwd=plugin, env=params.env, input=json.dumps(prompt), text=True,
                                  capture_output=True, check=True)
            assert own["memory_id"] in json.dumps(json.loads(hook.stdout)["injectSteps"])
            assert not (await _call(client, "search", {"query": "unboundwrite"}))["hits"]
            await _assert_inaccessible(client, other, "bluezircon")
            # No roots/list_changed notification: the next tool call still has to
            # recheck. A write must never be routed to either A or B after a switch.
            roots[:] = [b.as_uri()]
            lookup_count = requested_roots
            await denied(client, "get", {"memory_id": own["memory_id"]})
            assert requested_roots > lookup_count
            await denied(client, "record", {"content": "switchwrite", "idempotency_key": "switched"})
            roots[:] = [a.as_uri()]
            await denied(client, "get", {"memory_id": own["memory_id"]})

        roots[:] = [b.as_uri()]
        async with host() as new_process:
            assert (await _call(new_process, "get", {"memory_id": other["memory_id"]}))["content"] == "scopecanary from B bluezircon"
            assert not (await _call(new_process, "search", {"query": "switchwrite"}))["hits"]
            await _assert_inaccessible(new_process, own, "greenradar")
            # Host failure after a successful bind must not reuse the old root.
            callback_fails = True
            await denied(new_process, "get", {"memory_id": other["memory_id"]})
            callback_fails = False

        for invalid in ([a.as_uri(), b.as_uri()], ["file://remote.invalid" + a.as_uri()[7:]],
                        [(tmp_path / "missing/api").as_uri()]):
            roots[:] = invalid
            async with host() as ambiguous:
                await denied(ambiguous, "get", {"memory_id": own["memory_id"]})
        async with host(with_roots=False) as unsupported:
            await denied(unsupported, "get", {"memory_id": own["memory_id"]})
        # Some invocations provide an actual repo cwd as well as PLUGIN_ROOT.
        # A conflicting roots reply must never silently use the cwd's database.
        bound = params.model_copy(update={"cwd": a, "env": {**_env(home, a), "PLUGIN_ROOT": str(plugin)}})
        roots[:] = [b.as_uri()]
        async with host(bound) as conflict:
            await denied(conflict, "get", {"memory_id": own["memory_id"]})
        roots[:] = [a.as_uri()]
        async with host(bound) as confirmed:
            assert (await _call(confirmed, "get", {"memory_id": own["memory_id"]}))["revision_id"] == own["revision_id"]
        async with host() as racing:
            await _call(racing, "status", {})
            # A stale roots/list reply for A arrives after a notification switched
            # the host to B. Neither project should receive the requested write.
            notify_during_reply = racing
            await denied(racing, "record", {"content": "notificationrace", "idempotency_key": "notification"})
            notify_during_reply = None
        async with _client(home, b, "claude", SERVER) as check_b:
            assert not (await _call(check_b, "search", {"query": "notificationrace"}))["hits"]
        async with _client(home, a, "claude", SERVER) as handoff:
            assert (await _call(handoff, "get", {"memory_id": own["memory_id"]}))["content"] == "scopecanary from A greenradar"
            assert not (await _call(handoff, "search", {"query": "switchwrite"}))["hits"]
            assert not (await _call(handoff, "search", {"query": "notificationrace"}))["hits"]

    anyio.run(scenario)
