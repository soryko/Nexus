"""End-to-end tests for integrations/antigravity-plugin scripts, gateway, hooks, and agy validation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from mcp import Client, StdioServerParameters, types

from nexus_memory.activity import ActivityJournal
from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository
from nexus_memory.transport.mcp_apps import ATLAS_RESOURCE_URI, CARD_RESOURCE_URI

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "integrations" / "antigravity-plugin"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    )


def _clean_env(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _load_script_module(name: str):
    import importlib.util

    prev_dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        spec = importlib.util.spec_from_file_location(f"ag_plugin_{name}", SCRIPTS_DIR / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.dont_write_bytecode = prev_dont_write


def test_mcp_gateway_status_diagnostics_and_bound_apps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prev_umask = os.umask(0o077)
    try:
        home = tmp_path / "home"
        home.mkdir(mode=0o700)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("PLUGIN_ROOT", str(PLUGIN_ROOT))

        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        repo = repo.resolve()

        unenrolled = tmp_path / "unenrolled"
        unenrolled.mkdir()
        _git(unenrolled, "init", "-q")
        unenrolled = unenrolled.resolve()

        scope_mod = _load_script_module("project_scope")
        observed = scope_mod.identity(repo / ".git")
        if observed["birth_time"] is None:
            monkeypatch.setattr(scope_mod, "identity", lambda _: {**observed, "birth_time": 987654.5})
        gw_mod = _load_script_module("mcp_gateway")

        class FakeSession:
            def __init__(self, roots: list[Path]) -> None:
                self._roots = roots

            def check_client_capability(self, _cap) -> bool:
                return True

            async def list_roots(self):
                return SimpleNamespace(roots=[SimpleNamespace(uri=r.as_uri()) for r in self._roots])

        # 1. Unenrolled status gives structured diagnostic without creating DB/registry, while search fails closed
        guard_un = gw_mod.GuardedService(None)
        server_un = gw_mod.create_server(guard_un, database=lambda: guard_un.database_path, activity_journal=lambda: guard_un.journal, enable_mcp_apps=True)
        server_un.middleware.insert(0, guard_un.authorize)

        async def check_unenrolled() -> None:
            res_status = await guard_un.authorize(
                SimpleNamespace(method="tools/call", params=SimpleNamespace(name="status"), session=FakeSession([unenrolled])),
                lambda _ctx: None,
            )
            assert res_status.is_error is False
            assert res_status.structured_content["scope_state"] == "unenrolled"
            assert res_status.structured_content["enroll_command"] is not None
            assert not (home / ".local/share/nexus-memory").exists()

            res_search = await guard_un.authorize(
                SimpleNamespace(method="tools/call", params=SimpleNamespace(name="search"), session=FakeSession([unenrolled])),
                lambda _ctx: None,
            )
            assert res_search.is_error is True
            assert "project_not_enrolled" in res_search.content[0].text

        anyio.run(check_unenrolled)

        # 2. APFS device drift status surfaces recover_command with UUID, while memory tools fail closed
        pid = scope_mod.enroll(repo)
        reg_path = scope_mod.private_base() / "registry.json"
        reg = json.loads(reg_path.read_bytes())
        reg["projects"][str(repo / ".git")]["device"] += 2
        reg_path.write_text(json.dumps(reg))

        guard_drift = gw_mod.GuardedService(None)

        async def check_drift() -> None:
            res_status = await guard_drift.authorize(
                SimpleNamespace(method="tools/call", params=SimpleNamespace(name="status"), session=FakeSession([repo])),
                lambda _ctx: None,
            )
            assert res_status.is_error is False
            assert res_status.structured_content["scope_state"] == "device_drift"
            assert res_status.structured_content["project_id"] == pid
            assert pid in res_status.structured_content["recover_command"]

            res_get = await guard_drift.authorize(
                SimpleNamespace(method="tools/call", params=SimpleNamespace(name="get"), session=FakeSession([repo])),
                lambda _ctx: None,
            )
            assert res_get.is_error is True
            assert "scope_device_drift" in res_get.content[0].text

        anyio.run(check_drift)
    finally:
        os.umask(prev_umask)


def test_mcp_gateway_stdio_enables_mcp_apps_and_records_activity(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "init")
    repo = repo.resolve()

    env = _clean_env(home)
    subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPTS_DIR / "launcher.py"), "enroll", str(repo)],
        env=env,
        check=True,
        capture_output=True,
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-B", str(SCRIPTS_DIR / "mcp_gateway.py")],
        env=env,
        cwd=PLUGIN_ROOT,
    )

    async def supply_roots(_ctx):
        return types.ListRootsResult(roots=[types.Root(uri=repo.as_uri())])

    async def scenario() -> None:
        async with Client(params, mode="legacy", list_roots_callback=supply_roots) as client:
            listed = await client.list_tools()
            assert "open_atlas" in {t.name for t in listed.tools}
            res = await client.list_resources()
            assert {str(r.uri) for r in res.resources} == {CARD_RESOURCE_URI, ATLAS_RESOURCE_URI}

            st = await client.call_tool("status", {})
            assert st.is_error is False
            assert st.structured_content["capture_policy"] == "explicit"

            rec = await client.call_tool(
                "record",
                {"content": "Verify plugin gateway", "kind": "decision", "idempotency_key": "gw-1"},
            )
            assert rec.is_error is False
            pid = st.structured_content["namespace"]
            db_path = home / ".local/share/nexus-memory/project-scopes-v1" / f"{pid}.sqlite3"
            events = ActivityJournal.open_readonly(ActivityJournal.path_for_store(db_path)).list_events(limit=10)
            assert any(e.kind == "tool_record" and e.outcome == "committed" for e in events)

    # Check if Client supports list_roots_callback; inspect fallback if signature differs
    try:
        import inspect

        sig = inspect.signature(Client.__init__)
        assert "list_roots_callback" in sig.parameters
    except AssertionError:
        pytest.skip("mcp.Client does not expose list_roots_callback in this version")
    anyio.run(scenario)


def test_hooks_start_and_recall_record_content_free_activity_and_omit_stop(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    repo = repo.resolve()
    env = _clean_env(home)

    unenrolled = tmp_path / "unenrolled"
    unenrolled.mkdir()
    _git(unenrolled, "init", "-q")

    # Unenrolled fails open cleanly without creating files
    un = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPTS_DIR / "hooks.py"), "recall"],
        input=json.dumps({"workspacePaths": [str(unenrolled)], "conversationId": "c1", "lastUserInput": "explain sqlite floor"}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert json.loads(un.stdout) == {"injectSteps": []}
    assert not (home / ".local/share/nexus-memory").exists()

    pid = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPTS_DIR / "launcher.py"), "enroll", str(repo)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    db_path = home / ".local/share/nexus-memory/project-scopes-v1" / f"{pid}.sqlite3"
    prev_umask = os.umask(0o077)
    try:
        svc = MemoryService(SQLiteRepository(db_path), Scope(pid, "local"))
        svc.record(MemoryInput("SQLite minimum version floor is 3.51.3", kind="constraint"), "hook-seed-1")
    finally:
        os.umask(prev_umask)

    # Start records session_start and returns explicit-only guidance
    st = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPTS_DIR / "hooks.py"), "start"],
        input=json.dumps({"workspacePaths": [str(repo)], "conversationId": "sess-1"}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    start_steps = json.loads(st.stdout)["injectSteps"]
    assert "explicitly requested" in start_steps[0]["systemMessage"]["systemMessage"]

    # Recall offers candidate once (without user_markdown), suppresses repeat, logs content-free telemetry
    rec1 = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPTS_DIR / "hooks.py"), "recall"],
        input=json.dumps({"workspacePaths": [str(repo)], "conversationId": "sess-1", "lastUserInput": "Explain SQLite version floor please"}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    steps1 = json.loads(rec1.stdout)["injectSteps"]
    assert len(steps1) == 1 and "ephemeralMessage" in steps1[0]
    assert "user_markdown" not in rec1.stdout

    rec2 = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPTS_DIR / "hooks.py"), "recall"],
        input=json.dumps({"workspacePaths": [str(repo)], "conversationId": "sess-1", "lastUserInput": "Explain SQLite version floor please"}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert json.loads(rec2.stdout) == {"injectSteps": []}

    # Stop subcommand is omitted / no-op
    stop = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPTS_DIR / "hooks.py"), "stop"],
        input=json.dumps({"workspacePaths": [str(repo)], "conversationId": "sess-1"}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert json.loads(stop.stdout) == {"injectSteps": []}

    events = ActivityJournal.open_readonly(ActivityJournal.path_for_store(db_path)).list_events(limit=10)
    kinds_outcomes = [(e.kind, e.outcome) for e in events]
    assert ("session_start", "bound") in kinds_outcomes
    assert ("recall", "offered") in kinds_outcomes
    assert ("recall", "suppressed") in kinds_outcomes
    # Verify prompt string never leaked into activity journal
    raw_dump = json.dumps([e.detail for e in events])
    assert "Explain SQLite" not in raw_dump


def test_agy_plugin_validate_strict_reports_zero_errors_and_zero_warnings() -> None:
    agy = shutil.which("agy")
    if not agy:
        pytest.skip("agy CLI not present")
    res = subprocess.run(
        [agy, "plugin", "validate", "--strict", "--json", str(PLUGIN_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"validate failed: {res.stdout}\n{res.stderr}"
    report = json.loads(res.stdout)
    bad = [f for f in report.get("findings", []) if f.get("severity") in ("error", "warning")]
    assert bad == [], f"unexpected errors/warnings: {bad}"
