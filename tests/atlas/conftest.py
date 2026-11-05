"""Disposable enrolled projects for the Atlas tests.

Each fixture gets a private HOME, a real Git checkout enrolled through the project-scope
integration, and a store written by the ordinary writer. Nothing touches the real home.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository

ROOT = Path(__file__).resolve().parents[2]
INTEGRATION = ROOT / "integrations" / "project_scope"
RETIRE = ROOT / "tools" / "retire_legacy_store.py"


def _load_project_scope():
    spec = importlib.util.spec_from_file_location("atlas_test_project_scope", INTEGRATION / "project_scope.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=env)


@dataclass
class Enrolled:
    scope_module: object
    home: Path
    checkout: Path
    project_id: str
    database: Path

    @property
    def scope(self) -> Scope:
        return Scope(self.project_id, "local")

    def resolve(self, workspace: Path):
        return self.scope_module.resolve(Path(workspace))

    def writer(self, index_profile: str = "exact") -> MemoryService:
        return MemoryService(SQLiteRepository(self.database, index_profile=index_profile), self.scope)

    def record(self, text: str, key: str, **kwargs) -> str:
        return self.writer().record(MemoryInput(text, **kwargs), key).memory_id

    def new_checkout(self, name: str) -> Path:
        path = self.home.parent / name
        path.mkdir()
        git(path, "init", "-q")
        git(path, "commit", "-q", "--allow-empty", "-m", "init")
        return path.resolve()


@pytest.fixture
def enrolled(tmp_path, monkeypatch) -> Enrolled:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    old_umask = os.umask(0o077)
    try:
        module = _load_project_scope()
        checkout = tmp_path / "repo"
        checkout.mkdir()
        git(checkout, "init", "-q")
        git(checkout, "commit", "-q", "--allow-empty", "-m", "init")
        checkout = checkout.resolve()
        project_id = module.enroll(checkout)
        database = module.resolve(checkout).database
        result = Enrolled(module, home.resolve(), checkout, project_id, database)
        result.record("Atlas fixture memory one", "k1", kind="observation", tags=["atlas"])
        yield result
    finally:
        os.umask(old_umask)
