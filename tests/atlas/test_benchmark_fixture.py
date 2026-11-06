"""The benchmark's fixture contract. These assertions exist before the generator and must fail without it."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from nexus_memory.domain.models import Scope
from nexus_memory.storage import SQLiteRepository

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("atlas_bench_fixture", ROOT / "benchmarks" / "atlas" / "fixture.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SMALL = dict(memories=60, hub_citers=12, hub_min=10, revised=5, forgotten=3, foreign=4)


@pytest.fixture
def bench():
    return _load()


@pytest.fixture
def store(tmp_path):
    database = tmp_path / "bench.sqlite3"
    SQLiteRepository(database)
    return database


def test_an_empty_store_fails_every_requirement(bench, store) -> None:
    problems = bench.check_fixture(store, Scope("bench-project", "local"), bench.Spec(**SMALL))
    assert len(problems) >= 6, problems


def test_the_generated_fixture_meets_the_contract(bench, store) -> None:
    spec = bench.Spec(**SMALL)
    scope = Scope("bench-project", "local")
    manifest = bench.build_fixture(store, scope, spec)
    assert bench.check_fixture(store, scope, spec) == []
    assert manifest["counts"]["active"] == 60
    assert {"hub", "revised", "isolated", "ordinary"} <= set(manifest["strata"])
    assert all(manifest["strata"][s] for s in ("hub", "revised", "isolated", "ordinary"))


@pytest.mark.parametrize("damage,expected", [
    ("DELETE FROM revision_references WHERE path='src/hub.py'", "hub"),
    ("UPDATE memories SET tombstoned=0 WHERE tombstoned=1", "forgotten"),
    ("DELETE FROM revisions WHERE parent_revision_id IS NOT NULL", "revision"),
    ("UPDATE memories SET namespace='bench-other' WHERE namespace='bench-foreign'", "foreign"),
], ids=["no-hub", "nothing-forgotten", "no-revisions", "no-foreign"])
def test_each_requirement_is_checked_on_its_own(bench, store, damage, expected) -> None:
    spec = bench.Spec(**SMALL)
    scope = Scope("bench-project", "local")
    bench.build_fixture(store, scope, spec)
    with sqlite3.connect(store) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute(damage)
    problems = bench.check_fixture(store, scope, spec)
    assert any(expected in p for p in problems), problems


def test_the_active_count_must_be_exact(bench, store) -> None:
    spec = bench.Spec(**SMALL)
    scope = Scope("bench-project", "local")
    bench.build_fixture(store, scope, spec)
    SQLiteRepository(store)  # no-op reopen
    from nexus_memory.domain.models import MemoryInput
    from nexus_memory.memory import MemoryService
    MemoryService(SQLiteRepository(store), scope).record(MemoryInput("one too many bench"), "extra")
    assert any("active" in p for p in bench.check_fixture(store, scope, spec))


def test_summary_uses_nearest_rank_percentiles_and_max_minus_min_spread() -> None:
    spec = importlib.util.spec_from_file_location("atlas_bench_run", ROOT / "benchmarks" / "atlas" / "run.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "benchmarks" / "atlas"))
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    result = module.summarize([float(n) for n in range(1, 101)])
    assert (result["n"], result["p50"], result["p95"], result["max"], result["min"], result["spread"]) == (100, 50.5, 95.0, 100.0, 1.0, 99.0)
    assert module.summarize([7.0])["p95"] == 7.0
