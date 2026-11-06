"""Counterexamples for the dev-m1 corpus and the way its labels are computed.

The labels are the thing that must not rot. Each check below is a way the set could look
correct and mean nothing.

    <venv>/bin/python -m pytest benchmarks/agent/test_dev_m1.py -q
"""
from __future__ import annotations

import json
import os
import re
import subprocess
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


# ---------------------------------------------------------------------------------------
# Controls on the LEAKAGE SCANS themselves.
#
# The scans were published as "none" on all four tasks while gathering no evidence at all:
# no A2-R `fixtures.json` records a `clone`, so `fix_added_tokens` returned an empty set and
# an empty set matches nothing. Every check below distinguishes "looked and found nothing"
# from "never looked", in one direction or the other.
# ---------------------------------------------------------------------------------------

def _fake_scratch(td: Path, task: str = "k1", *, clone: bool = True,
                  checks: bool = True, visible: bool = True,
                  new_identifier: str | None = None) -> tuple[Path, str | None]:
    """A minimal scratch tree plus a real git clone whose fix is under our control."""
    base = td / "scratch" / "c45" / f"run-{task}" / "base"
    (base / task / "src" / "pkg").mkdir(parents=True)
    (base / task / "src" / "pkg" / "core.py").write_text(
        "def existing_helper(value):\n    return value\n")
    if visible:
        (base / task / "tests").mkdir(parents=True)
        (base / task / "tests" / "test_visible.py").write_text("def test_visible():\n    pass\n")
    if checks:
        (base / "checks" / task).mkdir(parents=True)
        (base / "checks" / task / "test_hidden.py").write_text(
            "def test_hidden_acceptance():\n    pass\n")

    src_clone = None
    if clone:
        src_clone = td / "clone"
        (src_clone / "src" / "pkg").mkdir(parents=True)
        (src_clone / "src" / "pkg" / "core.py").write_text(
            "def existing_helper(value):\n    return value\n")
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
               "PATH": os.environ.get("PATH", "")}
        run = lambda *a: subprocess.run(["git", "-C", str(src_clone), *a],
                                        capture_output=True, text=True, env=env, check=True)
        run("init", "-q")
        run("add", "-A"); run("commit", "-qm", "pre")
        pre = run("rev-parse", "HEAD").stdout.strip()
        added = f"def {new_identifier}(value):\n    return value\n" if new_identifier else \
                "def existing_helper(value):\n    return value + 0\n"
        (src_clone / "src" / "pkg" / "core.py").write_text(
            "def existing_helper(value):\n    return value\n" + added)
        run("add", "-A"); run("commit", "-qm", "fix")
        fix = run("rev-parse", "HEAD").stdout.strip()
    else:
        pre, fix = "aaaaaaaa", "bbbbbbbb"

    (base / "fixtures.json").write_text(json.dumps(
        [{"task": task, "pre_fix": pre, "fix": fix,
          **({"clone": str(src_clone)} if clone else {})}]))
    return td / "scratch", (str(src_clone) if clone else None)


def test_a_missing_clone_is_unresolved_and_never_no_leakage(monkeypatch):
    """The defect as it stood: no A2-R fixture records a clone, so the scan returned an empty
    token set and four tasks published as clean.

    `_config_clone` is silenced here so the missing-clone path is exercised on its own. With
    it live, a fixture naming no clone falls back to the configured one and fails a revision
    later instead -- also `Unresolved`, and covered by the next check."""
    import tempfile
    monkeypatch.setattr(V, "_config_clone", lambda: None)
    with tempfile.TemporaryDirectory() as t:
        scratch, _ = _fake_scratch(Path(t), clone=False)
        with pytest.raises(V.Unresolved, match="no clone recorded"):
            V.fix_added_tokens(scratch, "k1", clone=None)


def test_the_configured_clone_is_used_when_the_fixture_names_none(monkeypatch):
    """Every A2-R fixture is in this state: no `clone` key, so the path has to come from
    gitignored `a1-config.json` at runtime.

    This asserts the fallback POSITIVELY -- the configured clone is reached and the scan
    completes on it -- rather than asserting which refusal fires. The earlier version took
    the fixture's word for nothing and `_config_clone`'s word for everything: it called
    `fix_added_tokens` with the host's real `a1-config.json` live, so on a machine carrying
    that file the refusal came from `git diff` and on a machine without it from "no clone
    recorded". Both are `Unresolved`, so the safety property held on both -- but the test
    named a branch, and which branch ran was a fact about the host. It passed here and
    failed in CI. The clone is supplied by the test now, so the assertion is the same
    everywhere."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        scratch, clone = _fake_scratch(Path(t), new_identifier="leaked_marker")
        # the fixture names no clone -- exactly the A2-R state
        spec = json.loads((scratch / "c45/run-k1/base/fixtures.json").read_text())
        spec[0].pop("clone")
        (scratch / "c45/run-k1/base/fixtures.json").write_text(json.dumps(spec))
        monkeypatch.setattr(V, "_config_clone", lambda: clone)
        assert "leaked_marker" in V.fix_added_tokens(scratch, "k1", clone=None)


def test_without_a_configured_clone_the_same_fixture_refuses(monkeypatch):
    """The other half of the fallback, pinned separately so neither host's state decides
    which one runs. No fixture clone and no configured clone is `Unresolved` -- never an
    empty token set, which is what published as "none" on four tasks."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        scratch, _ = _fake_scratch(Path(t), new_identifier="leaked_marker")
        spec = json.loads((scratch / "c45/run-k1/base/fixtures.json").read_text())
        spec[0].pop("clone")
        (scratch / "c45/run-k1/base/fixtures.json").write_text(json.dumps(spec))
        monkeypatch.setattr(V, "_config_clone", lambda: None)
        with pytest.raises(V.Unresolved, match="no clone recorded"):
            V.fix_added_tokens(scratch, "k1", clone=None)


def test_a_failed_git_diff_is_unresolved():
    """A clone that exists but does not carry the revisions must refuse, not return `set()`.
    `git diff` exits nonzero here and the previous version ignored the status entirely."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        scratch, clone = _fake_scratch(Path(t))
        spec = json.loads((scratch / "c45/run-k1/base/fixtures.json").read_text())
        spec[0]["fix"] = "deadbeefdeadbeef"
        (scratch / "c45/run-k1/base/fixtures.json").write_text(json.dumps(spec))
        with pytest.raises(V.Unresolved, match="git diff"):
            V.fix_added_tokens(scratch, "k1", clone=clone)


def test_missing_hidden_checks_are_unresolved():
    """An absent checks directory yielded an empty name set, which matches no memory and
    reads exactly like a clean scan."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        scratch, _ = _fake_scratch(Path(t), checks=False)
        with pytest.raises(V.Unresolved, match="hidden checks directory"):
            V.hidden_check_names(scratch, "k1")


def test_missing_visible_tree_is_unresolved():
    """'hidden and not visible' needs both halves. Without the visible tree the subtraction
    is against nothing, which OVER-reports rather than under-reports -- still not a measured
    answer."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        scratch, _ = _fake_scratch(Path(t), visible=False)
        with pytest.raises(V.Unresolved, match="visible test tree"):
            V.hidden_check_names(scratch, "k1")


def test_a_deliberately_contaminated_memory_is_caught():
    """The positive control. A scan that never fires is indistinguishable from a clean
    corpus, so a memory naming an identifier the fix introduces must be detected -- and the
    same scan must leave an uncontaminated memory alone."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        scratch, clone = _fake_scratch(Path(t), new_identifier="reconcile_unset_sentinel")
        tokens = V.fix_added_tokens(scratch, "k1", clone=clone)
        assert "reconcile_unset_sentinel" in tokens
        contaminated = "the fix adds reconcile_unset_sentinel to core.py"
        clean = "run the suite with PYTHONPATH=src and an interpreter carrying pytest"
        grab = lambda s: set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", s)) & tokens
        assert grab(contaminated) == {"reconcile_unset_sentinel"}
        assert grab(clean) == set()


def test_prose_in_the_fix_is_not_an_identifier():
    """k4's fix adds a docstring saying 'the invocation order takes precedence over the
    declaration order'. Scanning that as code flagged six memories for using ordinary English
    about unrelated subjects -- a check that can never pass, which is as uninformative as one
    that can never fire."""
    block = ('def f():\n    """Returns all declared parameters.\n\n'
             '    Invocation order takes precedence over declaration order.\n    """\n'
             '    weird_new_name = 1\n')
    tokens = V._code_tokens(block)
    assert "weird_new_name" in tokens
    for prose in ("precedence", "declaration", "declared", "Invocation"):
        assert prose not in tokens, prose


def test_an_unterminated_docstring_is_still_stripped():
    """Added lines are not valid Python: a docstring can open on a `+` line and close on an
    unchanged one. An unmatched opener must strip to the end of the block, or the prose
    filter silently stops working on exactly the diffs that need it."""
    tokens = V._code_tokens('    """Invocation order takes precedence\n    over declaration\n')
    assert tokens == set(), tokens


def test_the_scan_scope_is_recorded_as_limited():
    """Identifier scans compare tokens. They cannot establish that no memory conveys a fix in
    different words, and the report must say so rather than implying coverage it lacks."""
    import inspect
    src = inspect.getsource(V.build)
    assert "leakage_scan_scope" in src
    assert "SEMANTICALLY" in src


def test_unresolved_scans_do_not_exit_zero():
    """Exit status is the verdict. 0 must mean 'looked and found nothing'."""
    import inspect
    src = inspect.getsource(V.main)
    assert "return 3" in src and "return 1" in src
