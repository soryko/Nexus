"""Negative controls: each mutation disables one invariant and must fail its designated scenario.

Failing more than the designated scenario is recorded, not treated as a defect; a mutation
that failed nothing would be investigated for equivalence and coverage before the invariant
was called unguarded. The full matrix is a development run (NEXUS_MUTATION_MATRIX=1).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from b2a_scenarios import SCENARIOS

from nexus_memory.domain import models
from nexus_memory.domain.errors import StorageIntegrityError
from nexus_memory.domain.models import MemoryInput, Scope, TreeEntry
from nexus_memory.git.cli import GitCli, GitCliVerifier
from nexus_memory.memory.service import MemoryService
from nexus_memory.storage import SQLiteRepository


def skip_path_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models, "validate_reference_path", lambda path: path)


def empty_output_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    original = MemoryService._accept

    def accept(path, entries):
        if not entries:
            return TreeEntry("100644", "blob", "0" * 40, path)
        return original(path, entries)

    monkeypatch.setattr(MemoryService, "_accept", staticmethod(accept))


def drop_no_replace_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GitCli, "CONTROLS", tuple(flag for flag in GitCli.CONTROLS if flag != "--no-replace-objects"))


def accept_differing_pathname(monkeypatch: pytest.MonkeyPatch) -> None:
    original = MemoryService._accept

    def accept(path, entries):
        return original(entries[0].path if len(entries) == 1 else path, entries)

    monkeypatch.setattr(MemoryService, "_accept", staticmethod(accept))


def drop_full_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GitCliVerifier, "LS_TREE_FLAGS", ("-z",))


def accept_any_mode_or_type(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus_memory.domain.errors import PathNotInCommit

    def accept(path, entries):
        if len(entries) != 1 or entries[0].path != path:
            raise PathNotInCommit("path is not in that commit")
        return entries[0]

    monkeypatch.setattr(MemoryService, "_accept", staticmethod(accept))


def resolve_per_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(self, references):
        return {reference.effective_spec: self._resolve_one(reference.effective_spec) for reference in references}

    monkeypatch.setattr(MemoryService, "_resolve_specs", resolve)


def verify_inside_the_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold the writer transaction across verification, which is the invariant's negation.

    One ``BEGIN IMMEDIATE`` is opened and it is the only one open while git runs, which is
    what an implementation that had never separated verification from the write would do.
    An earlier version wrapped ``record`` instead, so the storage write underneath opened
    a second writer transaction on a second connection and blocked on the first. Measured
    at 0b0a217: that mutation failed six of the eight scenarios, single-threaded and with
    no verifier involved, surviving only the two whose writes are all meant to be refused
    anyway. Its failure of the lock boundary said nothing about the lock boundary.
    """
    original = MemoryService._verify

    def verify(self, references):
        with self.repository._transaction():
            return original(self, references)

    monkeypatch.setattr(MemoryService, "_verify", verify)


# A scenario detects a mutation by an assertion that no longer holds, or by a guard that
# no longer raises - pytest reports the latter as ``Failed``, which is a BaseException and
# not an Exception. Naming the expected outcome per mutation is the point: a bare
# ``BaseException`` also accepts an interpreter error, a KeyboardInterrupt, or a mutation
# that is simply broken code, none of which establish that the invariant was exercised.
DETECTED = (AssertionError, pytest.fail.Exception)

MUTATIONS = {
    "skip_path_validation": (skip_path_validation, "unsafe_paths", DETECTED),
    "empty_output_is_success": (empty_output_is_success, "missing_objects", DETECTED),
    "drop_no_replace_objects": (drop_no_replace_objects, "one_literal_entry", DETECTED),
    "accept_differing_pathname": (accept_differing_pathname, "one_literal_entry", DETECTED),
    "drop_full_tree": (drop_full_tree, "one_literal_entry", DETECTED),
    "accept_any_mode_or_type": (accept_any_mode_or_type, "unsupported_types", DETECTED),
    "resolve_per_reference": (resolve_per_reference, "changing_refs", DETECTED),
    # The blocked write itself, not merely "something went wrong": the independent writer
    # waits out its busy timeout against the lock held across verification and the store
    # reports the refusal. Reaching the scenario's own elapsed-time assertion instead
    # would also be a detection, so both are accepted and nothing else is.
    "verify_inside_the_transaction": (verify_inside_the_transaction, "lock_boundary",
                                      (StorageIntegrityError, *DETECTED)),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutation_fails_its_designated_scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    mutate, designated, expected = MUTATIONS[name]
    mutate(monkeypatch)
    with pytest.raises(expected):
        SCENARIOS[designated](tmp_path)


def test_the_lock_mutation_leaves_an_unreferenced_write_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mutation must change the lock boundary and nothing else.

    A mutation that cannot complete an ordinary write fails its designated scenario for a
    reason unrelated to the invariant, which is how the previous one passed while testing
    nothing. This is the negative control on the control.
    """
    verify_inside_the_transaction(monkeypatch)
    service = MemoryService(SQLiteRepository(tmp_path / "memory.sqlite3"), Scope("mutation", "local"))
    receipt = service.record(MemoryInput("no references, no verifier"), "k")
    assert service.get(receipt.memory_id).content == "no references, no verifier"


@pytest.mark.skipif(not os.environ.get("NEXUS_MUTATION_MATRIX"), reason="development run: set NEXUS_MUTATION_MATRIX=1")
def test_mutation_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Every mutation against every scenario, printed as a matrix for investigation."""
    lines = ["", f"{'mutation':32s} " + " ".join(f"{name[:12]:>12s}" for name in sorted(SCENARIOS))]
    for mutation in sorted(MUTATIONS):
        mutate, designated, _ = MUTATIONS[mutation]
        cells = []
        for scenario in sorted(SCENARIOS):
            with monkeypatch.context() as context:
                mutate(context)
                directory = tmp_path / mutation / scenario
                directory.mkdir(parents=True)
                try:
                    SCENARIOS[scenario](directory)
                except BaseException:  # noqa: BLE001 - any failure is the signal
                    cells.append("FAIL*" if scenario == designated else "fail")
                else:
                    cells.append("MISS*" if scenario == designated else "pass")
        lines.append(f"{mutation:32s} " + " ".join(f"{cell:>12s}" for cell in cells))
    with capsys.disabled():
        print("\n".join(lines))
