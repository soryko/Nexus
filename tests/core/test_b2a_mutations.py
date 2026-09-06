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
from nexus_memory.domain.models import TreeEntry
from nexus_memory.git.cli import GitCli, GitCliVerifier
from nexus_memory.memory.service import MemoryService


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
    original = MemoryService.record

    def record(self, item, key):
        with self.repository._transaction():
            return original(self, item, key)

    monkeypatch.setattr(MemoryService, "record", record)


MUTATIONS = {
    "skip_path_validation": (skip_path_validation, "unsafe_paths"),
    "empty_output_is_success": (empty_output_is_success, "missing_objects"),
    "drop_no_replace_objects": (drop_no_replace_objects, "one_literal_entry"),
    "accept_differing_pathname": (accept_differing_pathname, "one_literal_entry"),
    "drop_full_tree": (drop_full_tree, "one_literal_entry"),
    "accept_any_mode_or_type": (accept_any_mode_or_type, "unsupported_types"),
    "resolve_per_reference": (resolve_per_reference, "changing_refs"),
    "verify_inside_the_transaction": (verify_inside_the_transaction, "lock_boundary"),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutation_fails_its_designated_scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    mutate, designated = MUTATIONS[name]
    mutate(monkeypatch)
    with pytest.raises(BaseException):
        SCENARIOS[designated](tmp_path)


@pytest.mark.skipif(not os.environ.get("NEXUS_MUTATION_MATRIX"), reason="development run: set NEXUS_MUTATION_MATRIX=1")
def test_mutation_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Every mutation against every scenario, printed as a matrix for investigation."""
    lines = ["", f"{'mutation':32s} " + " ".join(f"{name[:12]:>12s}" for name in sorted(SCENARIOS))]
    for mutation in sorted(MUTATIONS):
        mutate, designated = MUTATIONS[mutation]
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
