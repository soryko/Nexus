"""Read-only NDJSON stdio worker powering Antigravity's Nexus Studio sidecar.

Every request supplies a workspace path or ``file://`` URI and an action name.
The worker strictly inspects enrollment through ``project_scope.inspect_scope``
and serves read-only views from ``AtlasQueries``, ``ActivityJournal``, and
``GitCliVerifier`` without creating databases, activity files, registry
entries, or Git checkout tokens.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import platform
import shlex
import sqlite3
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from nexus_memory.activity import ActivityJournal
from nexus_memory.atlas.binding import AtlasBinding, BindingUnavailable, open_binding
from nexus_memory.atlas.queries import AtlasQueries
from nexus_memory.domain.errors import InvalidInput, MemoryNotFound, NexusError, StorageIntegrityError
from nexus_memory.domain.models import HeadComparison, Scope, SearchQuery
from nexus_memory.git import GitCli, GitCliVerifier, read_token
from nexus_memory.memory import MemoryService

_MAX_LINE_BYTES = 256 * 1024


def _load_scope_module():
    """Load the trusted project_scope resolver from configured or standard locations."""
    existing = sys.modules.get("project_scope")
    if existing is not None and hasattr(existing, "inspect_scope") and hasattr(existing, "resolve"):
        return existing

    candidates: list[Path] = []
    env_dir = os.environ.get("NEXUS_PROJECT_SCOPE_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path(__file__).resolve().parents[3] / "integrations" / "project_scope")
    candidates.append(Path.home() / ".local" / "share" / "nexus-memory" / "project-scopes-v1" / "bin")

    for directory in candidates:
        module_path = directory / "project_scope.py"
        if module_path.is_file():
            spec = importlib.util.spec_from_file_location("nexus_studio_project_scope", module_path)
            if spec is not None and spec.loader is not None:
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop(spec.name, None)
                    raise
                return module
    raise RuntimeError("project_scope module could not be located")


def _service_version() -> str:
    try:
        return version("nexus-memory")
    except PackageNotFoundError:
        return "unknown"


def _parse_workspace_entry(raw: object) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidInput("workspace must be a non-empty path or file:// URI")
    text = raw.strip()
    if text.startswith("file:"):
        uri = urlsplit(text)
        if uri.scheme != "file" or uri.netloc not in ("", "localhost") or uri.query or uri.fragment:
            raise InvalidInput("workspace must be a local file URI")
        decoded = unquote(uri.path, errors="strict")
        if not Path(decoded).is_absolute() or decoded.startswith("//"):
            raise InvalidInput("workspace must be an absolute local directory")
        return Path(decoded)
    path = Path(text)
    if not path.is_absolute():
        raise InvalidInput("workspace must be an absolute path")
    return path


def _normalize_workspace(workspace: object) -> tuple[str, Path | None, str]:
    """Return ('ok', path, '') or (error_state, None, message)."""
    if workspace is None:
        return "empty", None, "no active workspace selected"
    if isinstance(workspace, (list, tuple)):
        if len(workspace) == 0:
            return "empty", None, "no active workspace selected"
        if len(workspace) > 1:
            return "ambiguous", None, "multiple workspace folders are open; select a single enrolled checkout"
        raw = workspace[0]
    else:
        raw = workspace
    try:
        return "ok", _parse_workspace_entry(raw), ""
    except (InvalidInput, ValueError) as error:
        return "invalid", None, str(error)


def _json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _zero_health() -> dict[str, Any]:
    return {
        "total_events": 0,
        "events_today": 0,
        "errors_24h": 0,
        "last_event_ts": None,
        "last_error_ts": None,
        "last_error_code": None,
        "counts_by_category": {"recall": 0, "read": 0, "write": 0, "error": 0},
    }


def _zero_overview() -> dict[str, Any]:
    return {"total": 0, "counts": [], "generation": 0}


def _readonly_verifier(repo, scope: Scope, checkout: Path):
    """Build a read-only GitCliVerifier ONLY if the checkout is already tokenized and bound."""
    git = GitCli()
    try:
        info = git.inspect(checkout)
    except NexusError:
        return None, None
    try:
        token = read_token(info.common_dir)
    except NexusError:
        return None, None
    if token is None:
        return None, None
    try:
        binding = repo.checkout_binding(scope, token)
    except NexusError:
        return None, None
    if binding is None or binding.object_format != info.object_format:
        return None, None
    return binding, GitCliVerifier(git, checkout, binding.object_format, info.common_dir, token)


class StudioWorker:
    """Stateful read-only handler caching verified AtlasBinding instances per checkout."""

    def __init__(self, scope_module: Any | None = None) -> None:
        self.scope_module = scope_module if scope_module is not None else _load_scope_module()
        self._bindings: dict[Path, AtlasBinding] = {}

    def _binding_for(self, checkout: Path) -> AtlasBinding:
        resolved_checkout = checkout.resolve()
        cached = self._bindings.get(resolved_checkout)
        if cached is not None:
            try:
                cached.check()
                return cached
            except NexusError:
                self._bindings.pop(resolved_checkout, None)
        binding = open_binding(resolved_checkout, self.scope_module.resolve)
        self._bindings[resolved_checkout] = binding
        return binding

    def _evict(self, checkout: Path | None) -> None:
        if checkout is not None:
            try:
                self._bindings.pop(checkout.resolve(), None)
            except OSError:
                self._bindings.pop(checkout, None)

    def _require_bound(self, workspace: object) -> tuple[Any, Path]:
        kind, path, message = _normalize_workspace(workspace)
        if kind != "ok" or path is None:
            raise BindingUnavailable(message)
        diag = self.scope_module.inspect_scope(path)
        if diag.state != "bound" or diag.checkout is None or diag.database is None:
            self._evict(diag.checkout)
            raise BindingUnavailable(diag.message or f"project scope state is {diag.state}")
        return diag, diag.checkout

    def _status(self, workspace: object) -> dict[str, Any]:
        base_info = {
            "service_version": _service_version(),
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
        }
        kind, path, message = _normalize_workspace(workspace)
        if kind != "ok" or path is None:
            return {
                **base_info,
                "state": kind,
                "message": message,
                "project_id": None,
                "store_initialized": False,
                "recorded_device": None,
                "observed_device": None,
                "recover_command": None,
                "overview": _zero_overview(),
                "activity_health": _zero_health(),
            }

        diag = self.scope_module.inspect_scope(path)
        if diag.state != "bound":
            self._evict(diag.checkout)

        recover_command = None
        if diag.state == "device_drift" and diag.checkout is not None and diag.project_id is not None:
            launcher = Path(self.scope_module.__file__).resolve().with_name("launcher.py")
            recover_command = (
                f"{shlex.quote(sys.executable)} -I {shlex.quote(str(launcher))} "
                f"recover {shlex.quote(str(diag.checkout))} "
                f"--expected-project-id {shlex.quote(diag.project_id)}"
            )

        store_initialized = False
        overview = _zero_overview()
        activity_health = _zero_health()

        if diag.state == "bound" and diag.database is not None and diag.checkout is not None:
            if diag.database.exists():
                try:
                    binding = self._binding_for(diag.checkout)
                    overview = _json_safe(AtlasQueries(binding).overview())
                    store_initialized = True
                except NexusError as error:
                    self._evict(diag.checkout)
                    return {
                        **base_info,
                        "state": "invalid",
                        "message": str(error),
                        "project_id": diag.project_id,
                        "store_initialized": False,
                        "recorded_device": diag.recorded_device,
                        "observed_device": diag.observed_device,
                        "recover_command": None,
                        "overview": _zero_overview(),
                        "activity_health": _zero_health(),
                    }
            activity_path = ActivityJournal.path_for_store(diag.database)
            activity_health = _json_safe(ActivityJournal.open_readonly(activity_path).health_summary())

        return {
            **base_info,
            "state": diag.state,
            "message": diag.message,
            "project_id": diag.project_id,
            "store_initialized": store_initialized,
            "recorded_device": diag.recorded_device,
            "observed_device": diag.observed_device,
            "recover_command": recover_command,
            "overview": overview,
            "activity_health": activity_health,
        }

    def _activity(self, workspace: object, params: dict[str, Any]) -> dict[str, Any]:
        diag, _ = self._require_bound(workspace)
        limit = params.get("limit", 50)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise InvalidInput("limit must be between 1 and 200")
        category = params.get("category")
        if category is not None and category not in ("all", "recall", "read", "write", "error"):
            raise InvalidInput("invalid activity category")
        since_seq = params.get("since_seq")
        if since_seq is not None and type(since_seq) is not int:
            raise InvalidInput("since_seq must be an integer")
        journal = ActivityJournal.open_readonly(ActivityJournal.path_for_store(diag.database))
        return {
            "health": _json_safe(journal.health_summary()),
            "events": _json_safe(journal.list_events(limit=limit, category=category, since_seq=since_seq)),
        }

    def _queries_if_initialized(self, workspace: object) -> tuple[AtlasBinding | None, AtlasQueries | None]:
        diag, checkout = self._require_bound(workspace)
        if not diag.database.exists():
            return None, None
        binding = self._binding_for(checkout)
        return binding, AtlasQueries(binding)

    def _require_queries(self, workspace: object) -> tuple[AtlasBinding, AtlasQueries]:
        binding, queries = self._queries_if_initialized(workspace)
        if binding is None or queries is None:
            raise MemoryNotFound("memory not found")
        return binding, queries

    def _overview(self, workspace: object) -> dict[str, Any]:
        _, queries = self._queries_if_initialized(workspace)
        if queries is None:
            return _zero_overview()
        return _json_safe(queries.overview())

    def _search(self, workspace: object, params: dict[str, Any]) -> dict[str, Any]:
        binding, queries = self._queries_if_initialized(workspace)
        if queries is None or binding is None:
            return {"items": [], "matched": 0, "total": 0, "cursor": None, "generation": 0, "restarted": False}
        raw_kinds = params.get("kinds") or ()
        if isinstance(raw_kinds, str) or not isinstance(raw_kinds, (list, tuple)):
            raise InvalidInput("kinds must be a list of strings")
        raw_tags = params.get("tags_all") or ()
        if isinstance(raw_tags, str) or not isinstance(raw_tags, (list, tuple)):
            raise InvalidInput("tags_all must be a list of strings")
        limit = params.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("limit must be between 1 and 100")
        query = SearchQuery(
            query=params.get("query") or None,
            kinds=tuple(raw_kinds),
            tags_all=tuple(raw_tags),
            limit=limit,
            cursor=params.get("cursor") or None,
            repository=params.get("repository"),
            reference_paths=params.get("reference_paths"),
            reference_path_prefix=params.get("reference_path_prefix") or None,
            reference_commits=params.get("reference_commits"),
        )
        return _json_safe(queries.search(query))

    def _inspect(self, workspace: object, params: dict[str, Any]) -> dict[str, Any]:
        _, queries = self._require_queries(workspace)
        memory_id = params.get("memory_id")
        revision_id = params.get("revision_id")
        if not isinstance(memory_id, str) or not memory_id:
            raise InvalidInput("memory_id is required")
        if revision_id is not None and (not isinstance(revision_id, str) or not revision_id):
            raise InvalidInput("revision_id must be a non-empty string")
        inspection = queries.inspect(memory_id, revision_id)
        body = queries.body(inspection.memory_id, inspection.revision_id)
        payload = _json_safe(inspection)
        payload["content"] = body
        return payload

    def _history(self, workspace: object, params: dict[str, Any]) -> dict[str, Any]:
        _, queries = self._require_queries(workspace)
        memory_id = params.get("memory_id")
        if not isinstance(memory_id, str) or not memory_id:
            raise InvalidInput("memory_id is required")
        cursor = params.get("cursor")
        limit = params.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("limit must be between 1 and 100")
        return _json_safe(queries.history(memory_id, cursor, limit))

    def _compare_revision(self, workspace: object, params: dict[str, Any]) -> dict[str, Any]:
        _, queries = self._require_queries(workspace)
        memory_id = params.get("memory_id")
        revision_id = params.get("revision_id")
        if not isinstance(memory_id, str) or not memory_id or not isinstance(revision_id, str) or not revision_id:
            raise InvalidInput("memory_id and revision_id are required")
        return _json_safe(queries.compare(memory_id, revision_id))

    def _neighborhood(self, workspace: object, params: dict[str, Any]) -> dict[str, Any]:
        _, queries = self._require_queries(workspace)
        memory_id = params.get("memory_id")
        revision_id = params.get("revision_id")
        limit = params.get("limit", 25)
        if not isinstance(memory_id, str) or not memory_id:
            raise InvalidInput("memory_id is required")
        if type(limit) is not int or limit < 1 or limit > 100:
            raise InvalidInput("limit must be between 1 and 100")
        return _json_safe(queries.neighbors(memory_id, revision_id, limit))

    def _related(self, workspace: object, params: dict[str, Any]) -> dict[str, Any]:
        _, queries = self._require_queries(workspace)
        memory_id = params.get("memory_id")
        if not isinstance(memory_id, str) or not memory_id:
            raise InvalidInput("memory_id is required")
        limit = params.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("limit must be between 1 and 100")
        return _json_safe(queries.related(
            memory_id,
            tag=params.get("tag"),
            term=params.get("term"),
            limit=limit,
            cursor=params.get("cursor"),
        ))

    def _compare_at_head(self, workspace: object, params: dict[str, Any]) -> dict[str, Any]:
        binding, _ = self._require_queries(workspace)
        memory_id = params.get("memory_id")
        revision_id = params.get("revision_id")
        reference_ordinal = params.get("reference_ordinal")
        if not isinstance(memory_id, str) or not memory_id or not isinstance(revision_id, str) or not revision_id:
            raise InvalidInput("memory_id and revision_id are required")
        if type(reference_ordinal) is not int or reference_ordinal < 0:
            raise InvalidInput("reference_ordinal must be a nonnegative integer")
        repo = binding.reader()
        with repo.read_snapshot():
            repo_binding, verifier = _readonly_verifier(repo, binding.scope, binding._checkout)
            svc = MemoryService(repo, binding.scope, repo_binding, verifier)
            comparison = svc.compare_at_head(memory_id, revision_id, reference_ordinal)
        binding.check()
        return _json_safe(comparison)

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        req_id = request.get("id")
        action = request.get("action")
        workspace = request.get("workspace")
        params = request.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return {"id": req_id, "ok": False, "error": {"code": "invalid_input", "message": "params must be an object"}}
        try:
            if action == "status":
                data = self._status(workspace)
            elif action == "activity":
                data = self._activity(workspace, params)
            elif action == "overview":
                data = self._overview(workspace)
            elif action == "search":
                data = self._search(workspace, params)
            elif action == "inspect":
                data = self._inspect(workspace, params)
            elif action == "history":
                data = self._history(workspace, params)
            elif action == "compare_revision":
                data = self._compare_revision(workspace, params)
            elif action == "neighborhood":
                data = self._neighborhood(workspace, params)
            elif action == "related":
                data = self._related(workspace, params)
            elif action == "compare_at_head":
                data = self._compare_at_head(workspace, params)
            else:
                return {"id": req_id, "ok": False, "error": {"code": "unknown_action", "message": "unsupported action"}}
            return {"id": req_id, "ok": True, "data": data}
        except NexusError as error:
            return {"id": req_id, "ok": False, "error": {"code": error.code, "message": str(error)}}
        except (ValueError, TypeError) as error:
            return {"id": req_id, "ok": False, "error": {"code": "invalid_input", "message": str(error)}}
        except Exception:
            return {"id": req_id, "ok": False, "error": {"code": "internal_error", "message": "studio worker error"}}


def main(argv: list[str] | None = None) -> int:
    del argv
    os.umask(0o077)
    worker = StudioWorker()
    for raw_line in sys.stdin:
        if len(raw_line.encode("utf-8", "replace")) > _MAX_LINE_BYTES:
            reply = {"id": None, "ok": False, "error": {"code": "invalid_input", "message": "request exceeds size limit"}}
        else:
            line = raw_line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                reply = {"id": None, "ok": False, "error": {"code": "invalid_input", "message": "malformed JSON request"}}
            else:
                if not isinstance(req, dict):
                    reply = {"id": None, "ok": False, "error": {"code": "invalid_input", "message": "request must be an object"}}
                else:
                    reply = worker.handle(req)
        sys.stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
