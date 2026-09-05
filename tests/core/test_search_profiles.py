from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nexus_memory.domain.errors import UnsupportedRuntime
from nexus_memory.domain.models import MemoryInput, Scope, SearchQuery
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository
from nexus_memory.storage.sqlite import _is_code_shaped, _prose_text

PROSE = "Caption jobs expire after 72 hours in the queue."
CODE = "The per-asset encode work runs in transcode_worker.py and nowhere else."


def _service(path: Path, profile: str = "exact") -> MemoryService:
    return MemoryService(SQLiteRepository(path, index_profile=profile), Scope("dev", "local"))


def _found(service: MemoryService, text: str) -> set[str]:
    return {hit.memory_id for hit in service.search(SearchQuery(query=text, limit=20)).hits}


def _seed(service: MemoryService) -> dict[str, str]:
    prose = service.record(MemoryInput(PROSE, kind="constraint", tags=["captions"]), "k-prose")
    code = service.record(MemoryInput(CODE, kind="procedure", tags=["pipeline"]), "k-code")
    return {"prose": prose.memory_id, "code": code.memory_id,
            "prose_revision": prose.revision_id, "code_revision": code.revision_id}


def test_unknown_profile_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedRuntime):
        SQLiteRepository(tmp_path / "memory.sqlite3", index_profile="porter")


def test_exact_is_the_default_and_is_recorded(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    repository = SQLiteRepository(path)
    assert repository.index_profile == "exact"
    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT profile FROM search_profile WHERE id=1").fetchone() == ("exact",)
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('head_fts_stem','head_fts_prose')").fetchone()[0] == 0
    finally:
        db.close()


def test_exact_does_not_match_across_a_word_stem(tmp_path: Path) -> None:
    """The baseline behaviour the morphology work exists to change."""
    service = _service(tmp_path / "memory.sqlite3")
    ids = _seed(service)
    assert _found(service, "expire") == {ids["prose"]}
    assert _found(service, "expires") == set()


@pytest.mark.parametrize("profile", ["stem", "dual", "split"])
def test_stemming_profiles_match_across_a_word_stem(tmp_path: Path, profile: str) -> None:
    service = _service(tmp_path / "memory.sqlite3", profile)
    ids = _seed(service)
    assert _found(service, "expires") == {ids["prose"]}


@pytest.mark.parametrize("profile", ["exact", "stem", "dual", "split"])
def test_every_profile_still_finds_an_exact_path_reference(tmp_path: Path, profile: str) -> None:
    service = _service(tmp_path / "memory.sqlite3", profile)
    ids = _seed(service)
    assert ids["code"] in _found(service, "transcode_worker.py")


def test_split_keeps_identifiers_out_of_the_stemmed_index(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    service = _service(path, "split")
    _seed(service)
    db = sqlite3.connect(path)
    try:
        prose = [row[0] for row in db.execute("SELECT body FROM head_fts_prose").fetchall()]
    finally:
        db.close()
    assert any("transcode_worker.py" not in body and "encode" in body for body in prose)
    assert all("transcode_worker.py" not in body for body in prose)


def test_a_profile_switch_rebuilds_the_indexes_on_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    ids = _seed(_service(path, "exact"))
    switched = _service(path, "dual")
    assert _found(switched, "expires") == {ids["prose"]}
    back = _service(path, "exact")
    assert _found(back, "expires") == set()
    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT profile FROM search_profile WHERE id=1").fetchone() == ("exact",)
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='head_fts_stem'").fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.parametrize("profile", ["dual", "split"])
def test_forgetting_clears_the_auxiliary_index_too(tmp_path: Path, profile: str) -> None:
    service = _service(tmp_path / "memory.sqlite3", profile)
    ids = _seed(service)
    service.forget(ids["prose"], ids["prose_revision"], "k-forget")
    assert _found(service, "expires") == set()
    assert _found(service, "expire") == set()


def test_code_shape_classification() -> None:
    for chunk in ("transcode_worker.py", "/v3/assets/manifest", "AssetState.PUBLISHED", "caption_track_id", "mux2", "h264"):
        assert _is_code_shaped(chunk), chunk
    # CamelCase product names are deliberately treated as exact terms: stemming a name
    # is never right, and routing them to the exact index costs nothing when the stemmer
    # would have left them alone anyway.
    assert _is_code_shaped("WebVTT")
    for chunk in ("captions", "expire", "at-least-once", "publication.", "72", "queue"):
        assert not _is_code_shaped(chunk), chunk
    assert "transcode_worker.py" not in _prose_text(CODE)
    assert "encode" in _prose_text(CODE)
