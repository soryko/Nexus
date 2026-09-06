"""Temporary: proves this workflow's failure path, then is deleted.

A pipeline nobody has watched fail is a pipeline whose reporting is untested. This fails on
purpose and writes a diagnosis on its way out, so one run establishes both halves: the job
goes red rather than swallowing the status, and the evidence is downloadable afterwards.
"""
from __future__ import annotations

from diagnostics import preserve


def test_the_failure_path_leaves_downloadable_evidence() -> None:
    note = preserve("ci-probe", {"why": "deliberate failure, validating artifact upload"})
    raise AssertionError(f"deliberate failure to validate CI reporting; {note}")
