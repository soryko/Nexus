"""Counterexamples for the dev-m1 corpus and the way its labels are computed.

The labels are the thing that must not rot. Each check below is a way the set could look
correct and mean nothing.

    <venv>/bin/python -m pytest benchmarks/agent/test_dev_m1.py -q
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import build_dev_m1 as B                                               # noqa: E402
import verify_dev_m1 as V                                              # noqa: E402

CORPUS = json.loads((BENCH / "corpus-dev-m1.json").read_text())
BY_ID = {m["id"]: m for m in CORPUS["memories"]}


def test_every_memory_has_a_declared_subject():
    """A memory with no entry in SUBJECT is neither support nor on-subject -- it would fall
    out of every condition silently."""
    missing = [m["id"] for m in CORPUS["memories"] if m["id"] not in V.SUBJECT]
    assert missing == []


def test_a_probe_that_cannot_fail_is_not_a_probe():
    """`probe_tree` must return false for a claim the file refutes. A probe whose patterns
    match anything would make every memory 'true' and every task 'useful'."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src" / "click").mkdir(parents=True)
        (root / "src" / "click" / "core.py").write_text("nothing here\n")
        assert V.probe_tree({"file": "src/click/core.py", "all": [r"_close_callbacks"]},
                            root) == "false"
        assert V.probe_tree({"file": "src/click/core.py", "none": [r"nothing"]},
                            root) == "false"
        assert V.probe_tree({"file": "src/click/core.py", "all": [r"nothing"]},
                            root) == "true"


def test_a_missing_file_is_unknown_and_never_false():
    """h01's probe reads src/click/_utils.py, which does not exist at three of the four
    checkouts. Absent is not refuted: reporting it false would make those three tasks 'stale'
    on a file that was never there."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        assert V.probe_tree({"file": "src/click/_utils.py", "all": [r"X"]},
                            Path(td)) == "unknown"


def test_the_unnecessary_condition_contains_no_on_subject_memory():
    labels = {t: {"support": [m["id"] for m in CORPUS["memories"]
                              if not V.SUBJECT.get(m["id"], [])],
                  "useful": [], "stale": []} for t in V.TASKS}
    for t in V.TASKS:
        ids = V.condition_ids(CORPUS, labels, t, "unnecessary")
        assert all(t not in V.SUBJECT.get(i, []) for i in ids), (t, ids)
        assert all(BY_ID[i]["provenance"] != "distractor" for i in ids)


def test_the_unnecessary_condition_is_the_same_set_for_every_task():
    """It is the control. If it varied by task, a difference between tasks under it would be
    a difference in the corpus rather than in the task."""
    labels = {t: {"support": [m["id"] for m in CORPUS["memories"]
                              if not V.SUBJECT.get(m["id"], [])],
                  "useful": [], "stale": []} for t in V.TASKS}
    sets = {tuple(V.condition_ids(CORPUS, labels, t, "unnecessary")) for t in V.TASKS}
    assert len(sets) == 1


def test_a_constructed_contradiction_says_so():
    """m05 and m06 are contradictions nobody observed. They must not be readable as captured
    evidence, and their refutation must name where a reader can check it."""
    for mid in ("m05", "m06"):
        m = BY_ID[mid]
        assert m["provenance"] == "derived"
        assert m["synthetic_mechanism_test"] is True
        assert "CONSTRUCTED CONTRADICTION" in m["derived_from"]
        assert m["contradiction_discoverable_at"]


def test_captured_memories_are_carried_verbatim():
    """The frozen held-out corpus is not edited by being reused."""
    src = {m["id"]: m for m in
           json.loads((BENCH / "corpus-heldout-a1.json").read_text())["memories"]}
    for m in CORPUS["memories"]:
        if m["provenance"] == "captured":
            assert m["content"] == src[m["id"]]["content"]
            assert m["kind"] == src[m["id"]]["kind"]
            assert m["tags"] == src[m["id"]]["tags"]


def test_the_answer_key_fields_are_not_delivered():
    """`seed_store` and `render_notes` deliver content, kind and tags. Anything else in a
    memory is the evaluator's and must stay the evaluator's."""
    import seed_store
    import render_notes
    assert set(seed_store.DELIVERED_FIELDS) == {"content", "kind", "tags"}
    assert "probe" not in render_notes.DELIVERED
    assert "provenance" not in render_notes.DELIVERED
    assert "synthetic_mechanism_test" not in render_notes.DELIVERED


def test_distractors_bear_on_no_task():
    for m in CORPUS["memories"]:
        if m["provenance"] == "distractor":
            assert V.SUBJECT[m["id"]] == []


@pytest.mark.parametrize("task", V.TASKS)
def test_every_task_has_a_frozen_query(task):
    assert V.QUERIES[task].strip()


def test_the_four_conditions_are_named_and_no_more():
    assert V.CONDITIONS == ("useful", "unnecessary", "stale", "distracting")


def test_a_corpus_id_collision_would_be_caught():
    ids = [m["id"] for m in CORPUS["memories"]]
    assert len(ids) == len(set(ids))


def test_no_memory_quotes_a_hidden_check_name_pattern():
    """A cheap structural guard beside the measured one in `verify_dev_m1`: no memory may
    name a test FUNCTION. The measured check compares against each task's real hidden checks;
    this one fails even if that comparison stops running.

    A test FILE is not a test name -- `tests/test_options.py` is the visible suite's path and
    several captured memories name it as procedure. The guard excludes a match preceded by a
    path separator or followed by `.py`, and the two cases below are the control on that: if
    the exclusion ever widens to swallow a real function name, the second assert fails."""
    NAME = re.compile(r"(?<![/\w])test_[a-z_]{4,}(?!\.py)")
    assert not NAME.search("run PYTHONPATH=src pytest tests/test_options.py -q")
    assert NAME.search("the added test_flag_value_dual_options covers it")
    for m in CORPUS["memories"]:
        assert not NAME.search(m["content"]), (m["id"], m["content"][:120])
