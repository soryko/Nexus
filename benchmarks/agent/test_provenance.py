"""Controls on the provenance review itself.

The review is reading, not measurement, so the thing that can rot is its COVERAGE and its
honesty about its own strength. Each check below is a way it could look complete and mean
less than it says.

    <venv>/bin/python -m pytest benchmarks/agent/test_provenance.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import review_provenance as RP                                             # noqa: E402
import verify_dev_m1 as V                                                  # noqa: E402

CORPUS = json.loads((BENCH / "corpus-dev-m1.json").read_text())

#: The FROZEN review, computed where the clone exists. The fix-locality scan needs the
#: upstream clone, whose path lives in gitignored `a1-config.json` (A1's fixtures are
#: outside the repository and nothing in git records where). Recomputing it in CI would
#: make these checks assert a property of the host -- which is the defect
#: `test_the_configured_clone_is_used_when_the_fixture_names_none` had, passing here and
#: failing there. The scan's results are read from the freeze; the review's coverage and
#: its judgements are checked against the live table either way.
FROZEN = json.loads((BENCH / "provenance-dev-m1.json").read_text())


def test_every_memory_an_arm_can_receive_is_reviewed():
    """The `distracting` condition is the whole corpus, so an arm can receive any of the 24.
    Reviewing only the on-subject ones would leave 16 memories unexamined."""
    assert set(RP.REVIEW) == {m["id"] for m in CORPUS["memories"]}
    assert len(RP.REVIEW) == 24


def test_an_unreviewed_memory_refuses_rather_than_being_skipped():
    """A memory added to the corpus and not to the review must stop the build. Silently
    omitting it is how a corpus grows past its own review."""
    import pytest
    saved = RP.REVIEW.pop("x05")
    try:
        with pytest.raises(SystemExit, match="x05"):
            RP.build()
    finally:
        RP.REVIEW["x05"] = saved


def test_every_review_records_all_five_things_the_review_asked_for():
    for mid, r in RP.REVIEW.items():
        assert r["exposure"], mid                       # what the author had seen
        assert r["register"] in (RP.EXISTING, RP.GUIDANCE, RP.REPAIR), mid
        assert isinstance(r["suitable"], bool), mid
        assert len(r["note"]) > 40, mid                 # and why


def test_the_review_reports_its_own_scan_rule():
    assert "AS CODE" in FROZEN["fix_locality_rule"]
    assert "able to fire" in FROZEN["fix_locality_rule"]


def test_the_probe_gap_is_visible_for_every_memory():
    """`m02` is the worked example: three regular expressions, and prose that additionally
    claims construction-per-call and object comparison. A reader must be able to see that
    the probe is narrower than the paragraph it labels."""
    by_id = {r["id"]: r for r in FROZEN["rows"]}
    assert all("probe_supports_n" in r and "prose_claims_n" in r for r in FROZEN["rows"])
    assert by_id["m02"]["probe_supports_n"] == 3
    assert by_id["m02"]["prose_claims_n"] >= 2


def test_the_flagged_memory_stays_flagged():
    """m02 states both halves of k4's defect in the same clauses as the fix's own rationale
    comment. If a later edit unflags it, that must be a deliberate edit here."""
    assert RP.REVIEW["m02"]["flagged"] is True
    assert "k4" in V.SUBJECT["m02"]
    assert [r["id"] for r in FROZEN["rows"] if r["flagged"]] == ["m02"]


def test_the_freeze_was_computed_with_the_scan_actually_running():
    """`unresolved` is not `clean`. A freeze written on a host with no clone would carry
    empty localities for every memory, and every assertion below would pass vacuously."""
    assert FROZEN["fix_locality_state"] == "measured", FROZEN["fix_locality_state"]


def test_the_freeze_matches_the_corpus_it_reviews():
    """A corpus edited after the review was frozen is a corpus that is no longer reviewed."""
    import hashlib
    live = hashlib.sha256((BENCH / "corpus-dev-m1.json").read_bytes()).hexdigest()
    assert FROZEN["corpus_sha256"] == live, \
        "corpus-dev-m1.json changed since the provenance review was frozen; re-run " \
        "review_provenance.py on a host that has the clone"
    assert FROZEN["memories_reviewed"] == FROZEN["memories_in_corpus"] == 24


def test_the_scan_does_not_see_the_memory_the_reading_flags():
    """The finding, as a check. If a future scan DOES catch `m02`, this fails -- and that
    would be good news that must be read and written up rather than passing quietly."""
    by_id = {r["id"]: r for r in FROZEN["rows"]}
    assert by_id["m02"]["fix_locality"]["k4"] == []
    assert all(v == [] for v in by_id["m02"]["fix_locality"].values())


def test_the_scan_can_fire_at_all():
    """The other half. A scan that catches nothing anywhere is the `fix_added_tokens` defect
    again: an empty result reading as a clean one."""
    hits = [(r["id"], t, v) for r in FROZEN["rows"]
            for t, v in (r["fix_locality"] or {}).items() if v]
    assert hits, "the fix-locality scan found nothing on any memory -- it cannot fire"
    by_id = {r["id"]: r for r in FROZEN["rows"]}
    assert by_id["m01"]["fix_locality"]["k1"] == ["close"]


def test_the_headline_names_both_halves_of_the_finding():
    """The scan clears what the reading flags, AND flags what the reading clears. Half of
    that sentence on its own would read as an argument for the scan."""
    h = FROZEN["headline"]
    assert "m02" in h and "NOTHING" in h
    assert "flags memories the reading clears" in h


def test_code_tokens_exclude_prose_and_keep_identifiers():
    """The specificity repair, with its control. Two earlier versions of the sibling scan
    matched English and flagged 5-20 of 24 memories per task."""
    got = RP.code_tokens_of("the `flag_value` is read and the invocation order decides")
    assert "flag_value" in got
    assert "invocation" not in got and "order" not in got and "decides" not in got
    assert "_close_callbacks" in RP.code_tokens_of("held in _close_callbacks by close()")


def test_the_conclusion_is_not_overstated():
    rep = FROZEN
    assert rep["conclusion"] == "reviewed provenance with limited automated leakage checks"
    assert any("not measurement" in n for n in rep["not_established"])
    assert any("fix diffs" in n for n in rep["not_established"])
