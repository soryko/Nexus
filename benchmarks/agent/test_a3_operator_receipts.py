"""Exercise admission across a restart; no processes, credentials or model endpoints."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import a3_ledger as ledger
import a3_pipeline as pipeline
import isolation
import rehearse_a3_production as rehearsal
import run_a3


SCHEDULE = Path(__file__).parent / "schedule-a3.json"


def test_regular_forged_terminal_cannot_clear_an_interrupted_launch(tmp_path, monkeypatch):
    fixture = tmp_path / "run-k1" / "base" / "fixtures.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(json.dumps([{"task": "k1", "checks": [], "held_checks": "checks"}]))
    runner = SimpleNamespace(RUN=fixture.parent.parent, FIXTURE_BASE=fixture.parent,
                             PYTEST_PY="unused", BENCH=fixture.parent, ARMS={"nexus": {}},
                             CFG=SimpleNamespace(config_version="a3-v1-REHEARSAL"),
                             FORWARDER_PORT=rehearsal.FORWARDER_PORT, MAX_TURNS=45,
                             WALL_CLOCK_S=600, boundary_paths=lambda *_: ([], []),
                             provision_store=lambda *_: None, write_arm_profile=lambda *_: None,
                             run_identity=lambda: {}, patch_of=lambda *_: "",
                             score=lambda *_: {"passed": True, "summary": "passed"},
                             read_trace=lambda *_: {}, classify=lambda *_: "completed")
    calls = []

    def prepare(arm):
        work = runner.OUT / "arms" / arm / "repo"
        work.mkdir(parents=True)
        return work

    def forged(_arm, work):
        calls.append(work)
        (work.parent / "record.json").write_text('{"usage":{"input_tokens":2}}')
        (work.parent / "trace.jsonl").write_text('{"type":"result","usage":{"input_tokens":2}}\n')
        return {"result": {"usage": {"input_tokens": 13_000_000}}}

    runner.prepare, runner.invoke = prepare, forged
    monkeypatch.setenv("DEEPSEEK_API_KEY", rehearsal.STUB_KEY)
    monkeypatch.setattr(run_a3, "_load_runner", lambda *_: runner)
    monkeypatch.setattr(isolation, "check_boundary", lambda *a, **k: {
        "all_hold": True, "required_unresolved": [], "required_failed": [], "negative_controls": {}})
    with pytest.raises(FileExistsError):
        run_a3.run_pair(tmp_path, "k1", 1, SCHEDULE)
    before = ledger.read(tmp_path)
    assert before.tokens_known == 0 and len(before.unresolved) == 1
    # Simulate a fresh adapter: no in-memory previous-run state survives.
    monkeypatch.setattr(run_a3, "_load_runner", lambda *_: pytest.fail("retry reached runner"))
    assert run_a3.run_pair(tmp_path, "k2", 1, SCHEDULE) == 3
    assert len(calls) == 1


def test_legacy_sweep_reports_history_but_cannot_be_admitted_or_modified(tmp_path, monkeypatch):
    first = ledger.mark_launch(tmp_path, "k1", 1, "A", identity={}, max_turns=45,
                               wall_clock_s=600, prompt_digest="legacy")
    (first.parent / "record.json").write_text('{"usage":{"input_tokens":452}}')
    assert ledger.read(tmp_path).tokens_known == 452
    before = sorted((p.relative_to(tmp_path), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file())
    monkeypatch.setattr(run_a3, "_load_runner", lambda *_: pytest.fail("legacy root admitted"))
    assert run_a3.run_pair(tmp_path, "k2", 1, SCHEDULE) == 3
    assert sorted((p.relative_to(tmp_path), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()) == before


def test_operator_receipt_survives_changed_or_removed_candidate_evidence(tmp_path):
    import shutil
    with ledger.sweep_lock(tmp_path) as acquired:
        assert acquired
        ledger.start_epoch(tmp_path)
        ledger.claim_pair(tmp_path, "k1", 1)
        marker = ledger.mark_launch(tmp_path, "k1", 1, "A", identity={}, max_turns=45,
                                    wall_clock_s=600, prompt_digest="stub")
        ledger.record_outcome(tmp_path, "k1", 1, "A", {"input_tokens": 26_000_000})
    (marker.parent / "record.json").write_text('{"usage":{"input_tokens":2}}')
    assert ledger.read(tmp_path).tokens_known == 26_000_000
    shutil.rmtree(ledger.pair_directory(tmp_path, "k1", 1))
    # An operator may move artifacts out for inspection. The launch remains occupied.
    same = ledger.may_admit_pair(tmp_path, "k1", 1, threshold=80_000_000)
    assert not same["may_start"] and same["gate"] == "existing"
    other = ledger.may_admit_pair(tmp_path, "k2", 1)
    assert not other["may_start"] and other["tokens_known"] == 26_000_000


@pytest.mark.parametrize("shape", ["broken", "missing"])
def test_missing_or_bad_operator_format_never_falls_back(tmp_path, shape):
    first = ledger.mark_launch(tmp_path, "k1", 1, "B", identity={}, max_turns=45,
                               wall_clock_s=600, prompt_digest="old")
    (first.parent / "record.json").write_text('{"usage":{"input_tokens":2}}')
    area = tmp_path / ledger.OPERATOR_DIR
    area.mkdir()
    if shape == "broken":
        (area / ledger.FORMAT_FILE).write_text("unexpected")
    with pytest.raises(OSError):
        ledger.check_execution_root(tmp_path)
    assert ledger.read(tmp_path).tokens_known == 0
    assert ledger.fatal_stop(tmp_path)
