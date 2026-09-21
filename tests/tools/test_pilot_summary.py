"""The pilot reporter, checked against the templates a participant actually fills in.

`tools/summarize_pilot.py` reads the private log described by `docs/pilot-p1.md`. Its job is
narrow and mostly negative: refuse malformed input, and never let a missing observation read
as a measured one. So that is what this module tests, and it tests it two ways.

  * Against the **published templates**. `make_manifest` and `make_session` load
    `examples/pilot/*.json` and fill only the fields the protocol says a participant fills.
    A helper carrying its own copy of the record shape would keep passing while the file a
    reader actually copies drifted away from it -- the same failure `test_daily_use_recipe`
    exists to prevent, for the same reason.
  * Through the **real command line**. The CLI cases run the actual script in a subprocess
    with a real argv and read its real exit status, because exit status and stdout are what
    a person and a shell act on. A test that called a validation predicate and left `main`
    unexercised would prove nothing about the thing anyone runs.

The totals here are calculated by hand in the test body. That is the point of them: an
expected value computed by the code under test is not an expectation.

What none of this establishes: that the records are true, that the participant logged every
session, or that memory helped. Those are not properties of a reader. Nothing here opens a
database, launches a server, reaches the network, or touches a real pilot log.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "summarize_pilot.py"
MANIFEST_TEMPLATE = REPO / "examples" / "pilot" / "manifest.json"
SESSION_TEMPLATE = REPO / "examples" / "pilot" / "session.json"

TIMEOUT = 60
PILOT_START = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)


def _load_script():
    """Import the script by path. It lives in `tools/`, which is not an importable package."""
    assert SCRIPT.exists(), f"missing {SCRIPT.relative_to(REPO)}"
    spec = importlib.util.spec_from_file_location("summarize_pilot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pilot = _load_script()
summarize = pilot.summarize
load_pilot = pilot.load_pilot
PilotError = pilot.PilotError


def at(**delta) -> str:
    """An aware UTC timestamp offset from the pilot's start."""
    return (PILOT_START + timedelta(**delta)).isoformat()


def _template(path: Path) -> dict:
    assert path.exists(), (
        f"missing template {path.relative_to(REPO)} -- docs/pilot-p1.md tells a participant "
        f"to copy this file, so it has to exist and these tests have to be built from it"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def make_manifest(**overrides) -> dict:
    """The published blank, plus exactly the fields a private manifest has to fill.

    The database path names a file that is never created. `summarize_pilot.py` must not open
    it -- the reporter is offline by design -- and a test below checks that it does not.
    """
    manifest = _template(MANIFEST_TEMPLATE)
    manifest.update(
        {
            "client_version": "test-only-0.0.0",
            "server_command": sys.executable,
            "database_path": str(Path(tempfile.gettempdir()) / "nexus-pilot-never-opened.sqlite3"),
            "namespace": "pilot-test-namespace",
            "actor": "pilot-test-actor",
            "started_at": PILOT_START.isoformat(),
        }
    )
    manifest.update(overrides)
    return manifest


def make_session(session_id: str = "s01", **overrides) -> dict:
    """The published blank session, given an identity and a start. Everything else stays unknown."""
    session = _template(SESSION_TEMPLATE)
    session.update({"session_id": session_id, "started_at": PILOT_START.isoformat()})
    session.update(overrides)
    return session


def write_pilot(root: Path, manifest: dict, sessions: list[dict]) -> Path:
    """Lay out a private log on disk the way docs/pilot-p1.md describes it."""
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for session in sessions:
        name = f"{session.get('session_id', 'unnamed')}.json"
        (root / "sessions" / name).write_text(json.dumps(session, indent=2), encoding="utf-8")
    return root


def run_cli(log_dir) -> subprocess.CompletedProcess:
    """The script as a person runs it: real interpreter, real argv, real exit status."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--log-dir", str(log_dir)],
        capture_output=True, text=True, timeout=TIMEOUT,
    )


def refused(result: subprocess.CompletedProcess) -> str:
    """Assert the refusal contract -- exit 2, nothing on stdout -- and return the diagnosis."""
    assert result.returncode == 2, (
        f"expected exit 2 for invalid input, got {result.returncode}\n{result.stderr}"
    )
    assert result.stdout == "", (
        f"a refused run wrote to stdout; a partial report is worse than none: {result.stdout!r}"
    )
    assert result.stderr.strip(), "a refusal with no diagnosis leaves nothing to act on"
    return result.stderr


def accepted(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, f"expected exit 0, got {result.returncode}\n{result.stderr}"
    return json.loads(result.stdout)


class TestUnknownIsNotZero:
    """The distinction the whole format exists to keep. Everything else is bookkeeping."""

    def test_unknown_is_not_zero(self) -> None:
        rows = [make_session("s01", search_calls=0),
                make_session("s02", search_calls=None),
                make_session("s03", search_calls=2)]
        result = summarize(make_manifest(), rows)
        assert result["metric_coverage"]["search_calls"] == {
            "observed_sum": 2, "known_sessions": 2, "unknown_sessions": 1,
        }

    def test_a_metric_nobody_measured_sums_to_null_not_zero(self) -> None:
        rows = [make_session("s01"), make_session("s02")]
        result = summarize(make_manifest(), rows)
        for field in ("search_calls", "get_calls", "capture_calls",
                      "consult_seconds", "logging_seconds"):
            assert result["metric_coverage"][field] == {
                "observed_sum": None, "known_sessions": 0, "unknown_sessions": 2,
            }, f"{field} reported a total nobody measured"

    def test_an_empty_pilot_measures_nothing_rather_than_zero(self) -> None:
        result = summarize(make_manifest(), [])
        assert result["recorded_sessions"] == 0
        assert result["metric_coverage"]["consult_seconds"] == {
            "observed_sum": None, "known_sessions": 0, "unknown_sessions": 0,
        }
        assert result["reuse_evidence_entries"] == {
            "observed_entries": None, "linked_entries": None, "unlinked_entries": None,
        }
        assert set(result["friction_session_counts"].values()) == {None}

    def test_an_observed_zero_is_reported_as_zero(self) -> None:
        """0 is an observation. Only the absence of one is null."""
        rows = [make_session("s01", capture_calls=0, consult_seconds=0)]
        result = summarize(make_manifest(), rows)
        assert result["metric_coverage"]["capture_calls"] == {
            "observed_sum": 0, "known_sessions": 1, "unknown_sessions": 0,
        }
        assert result["metric_coverage"]["consult_seconds"]["observed_sum"] == 0

    def test_durations_sum_across_mixed_known_and_unknown(self) -> None:
        rows = [make_session("s01", consult_seconds=45.5, logging_seconds=90),
                make_session("s02", consult_seconds=14.5, logging_seconds=None),
                make_session("s03", consult_seconds=None, logging_seconds=30)]
        result = summarize(make_manifest(), rows)
        assert result["metric_coverage"]["consult_seconds"] == {
            "observed_sum": 60.0, "known_sessions": 2, "unknown_sessions": 1,
        }
        assert result["metric_coverage"]["logging_seconds"] == {
            "observed_sum": 120, "known_sessions": 2, "unknown_sessions": 1,
        }


class TestNothingDisappears:
    """Interrupted, abandoned and unreviewed work stays in the report."""

    def test_interrupted_and_abandoned_sessions_remain_visible(self) -> None:
        rows = [make_session("s01", state="open", impact="unknown"),
                make_session("s02", state="abandoned", impact="harmed")]
        result = summarize(make_manifest(), rows)
        assert result["recorded_sessions"] == 2
        assert result["state_counts"]["open"] == 1
        assert result["state_counts"]["abandoned"] == 1
        assert result["reported_impact_counts"]["unknown"] == 1
        assert result["reported_impact_counts"]["harmed"] == 1

    def test_not_attempted_consultation_is_counted_not_dropped(self) -> None:
        """Choosing not to consult is a finding about the routine, not a missing row."""
        rows = [make_session("s01", consultation="not_attempted", impact="not_used",
                             state="completed", task_outcome="completed"),
                make_session("s02", consultation="attempted", impact="neutral",
                             state="completed", task_outcome="completed")]
        result = summarize(make_manifest(), rows)
        assert result["consultation_counts"] == {
            "attempted": 1, "not_attempted": 1, "unknown": 0,
        }
        assert result["reported_impact_counts"]["not_used"] == 1

    def test_task_outcome_and_impact_are_counted_independently(self) -> None:
        """A failed task with helpful memory, and a completed one with none."""
        rows = [make_session("s01", state="completed", task_outcome="failed", impact="helped"),
                make_session("s02", state="completed", task_outcome="completed",
                             impact="not_used")]
        result = summarize(make_manifest(), rows)
        assert result["task_outcome_counts"]["failed"] == 1
        assert result["task_outcome_counts"]["completed"] == 1
        assert result["reported_impact_counts"]["helped"] == 1
        assert result["reported_impact_counts"]["not_used"] == 1


class TestNullVersusEmptyEvidence:
    """`null` is unreviewed; `[]` is reviewed and there was nothing. They must not merge."""

    def test_unreviewed_friction_is_null_and_reviewed_none_is_zero(self) -> None:
        unreviewed = summarize(make_manifest(), [make_session("s01", friction_codes=None)])
        assert unreviewed["friction_session_counts"]["stale_memory"] is None
        assert unreviewed["evidence_coverage"]["friction_codes"] == {
            "known_sessions": 0, "unknown_sessions": 1,
        }

        reviewed = summarize(make_manifest(), [make_session("s01", friction_codes=[])])
        assert reviewed["friction_session_counts"]["stale_memory"] == 0
        assert reviewed["evidence_coverage"]["friction_codes"] == {
            "known_sessions": 1, "unknown_sessions": 0,
        }

    def test_friction_counts_every_documented_code_once_per_session(self) -> None:
        rows = [make_session("s01", friction_codes=["stale_memory", "logging_burden"]),
                make_session("s02", friction_codes=["stale_memory"]),
                make_session("s03", friction_codes=None)]
        counts = summarize(make_manifest(), rows)["friction_session_counts"]
        assert counts["stale_memory"] == 2
        assert counts["logging_burden"] == 1
        assert counts["connection"] == 0
        assert set(counts) == set(pilot.FRICTION_CODES), "a documented code vanished from the report"

    def test_unreviewed_reuse_is_null_and_reviewed_none_is_zero(self) -> None:
        unreviewed = summarize(make_manifest(), [make_session("s01", reuse_evidence=None)])
        assert unreviewed["reuse_evidence_entries"] == {
            "observed_entries": None, "linked_entries": None, "unlinked_entries": None,
        }

        reviewed = summarize(make_manifest(), [make_session("s01", reuse_evidence=[])])
        assert reviewed["reuse_evidence_entries"] == {
            "observed_entries": 0, "linked_entries": 0, "unlinked_entries": 0,
        }
        assert reviewed["evidence_coverage"]["reuse_evidence"] == {
            "known_sessions": 1, "unknown_sessions": 0,
        }

    def test_one_reviewed_session_does_not_make_the_unreviewed_ones_zero(self) -> None:
        rows = [make_session("s01", friction_codes=["scope"]), make_session("s02")]
        result = summarize(make_manifest(), rows)
        assert result["friction_session_counts"]["scope"] == 1
        assert result["evidence_coverage"]["friction_codes"] == {
            "known_sessions": 1, "unknown_sessions": 1,
        }, "coverage has to stay beside the count, or the count reads as out of two"


def _capture(memory_id="mem-1", revision_id="rev-1") -> dict:
    return {"memory_id": memory_id, "revision_id": revision_id}


def _reuse(memory_id="mem-1", revision_id="rev-1", capture_session_id="s01",
           basis_checked=True, action_note="pinned the interpreter before rerunning") -> dict:
    return {
        "memory_id": memory_id, "revision_id": revision_id,
        "capture_session_id": capture_session_id, "basis_checked": basis_checked,
        "action_note": action_note,
    }


class TestCaptureLinks:
    """A claimed link is looked up. Resolving is not the same as being useful."""

    @staticmethod
    def _pair(**reuse_overrides) -> list[dict]:
        return [
            make_session("s01", started_at=at(minutes=0), captured_memories=[_capture()]),
            make_session("s02", started_at=at(hours=26),
                         reuse_evidence=[_reuse(**reuse_overrides)]),
        ]

    def test_a_resolved_link_is_counted_as_linked(self) -> None:
        result = summarize(make_manifest(), self._pair())
        assert result["reuse_evidence_entries"] == {
            "observed_entries": 1, "linked_entries": 1, "unlinked_entries": 0,
        }

    def test_a_revision_that_moved_since_capture_is_still_a_link(self) -> None:
        """Memories get corrected. Requiring the same revision would reject the normal case."""
        result = summarize(make_manifest(), self._pair(revision_id="rev-2"))
        assert result["reuse_evidence_entries"]["linked_entries"] == 1

    def test_an_unlinked_entry_is_kept_and_reported_as_unlinked(self) -> None:
        """A pre-pilot memory has no capture session here. It is not dropped for that."""
        rows = [make_session("s01", reuse_evidence=[_reuse(capture_session_id=None)])]
        assert summarize(make_manifest(), rows)["reuse_evidence_entries"] == {
            "observed_entries": 1, "linked_entries": 0, "unlinked_entries": 1,
        }

    def test_a_link_to_a_session_that_is_not_here_is_refused(self) -> None:
        rows = [make_session("s02", reuse_evidence=[_reuse(capture_session_id="s99")])]
        with pytest.raises(PilotError, match="not in this log"):
            summarize(make_manifest(), rows)

    def test_a_link_to_its_own_session_is_refused(self) -> None:
        """Capture then get, inside one session, is not cross-session reuse."""
        rows = [make_session("s01", captured_memories=[_capture()],
                             reuse_evidence=[_reuse(capture_session_id="s01")])]
        with pytest.raises(PilotError, match="own session"):
            summarize(make_manifest(), rows)

    def test_a_link_to_a_later_session_is_refused(self) -> None:
        rows = [
            make_session("s01", started_at=at(hours=26), captured_memories=[_capture()]),
            make_session("s02", started_at=at(minutes=0), reuse_evidence=[_reuse()]),
        ]
        with pytest.raises(PilotError, match="did not start earlier"):
            summarize(make_manifest(), rows)

    def test_a_link_to_a_session_that_tracked_no_captures_is_refused(self) -> None:
        rows = [
            make_session("s01", started_at=at(minutes=0), captured_memories=None),
            make_session("s02", started_at=at(hours=26), reuse_evidence=[_reuse()]),
        ]
        with pytest.raises(PilotError, match="records no captured memories"):
            summarize(make_manifest(), rows)

    def test_a_link_to_a_memory_that_session_did_not_capture_is_refused(self) -> None:
        rows = [
            make_session("s01", started_at=at(minutes=0),
                         captured_memories=[_capture(memory_id="mem-other")]),
            make_session("s02", started_at=at(hours=26), reuse_evidence=[_reuse()]),
        ]
        with pytest.raises(PilotError, match="does not record capturing"):
            summarize(make_manifest(), rows)


class TestProtocolDeviations:
    """Late and excess data is flagged and kept. The reporter does not police the pilot."""

    def test_sessions_past_the_session_limit_are_flagged_not_dropped(self) -> None:
        manifest = make_manifest()
        rows = [make_session(f"s{n:02d}", started_at=at(hours=n)) for n in range(1, 13)]
        result = summarize(manifest, rows)
        assert result["recorded_sessions"] == 12, "an over-limit session was discarded"
        flagged = [d["session_id"] for d in result["protocol_deviations"]
                   if d["deviation"] == "over_session_limit"]
        assert flagged == ["s11", "s12"], f"wrong sessions flagged: {result['protocol_deviations']}"

    def test_the_day_limit_boundary_falls_where_the_protocol_says(self) -> None:
        """Fourteen days is 336 hours; at the deadline is over it, an hour before is not."""
        rows = [make_session("s01", started_at=at(hours=335)),
                make_session("s02", started_at=at(hours=336))]
        result = summarize(make_manifest(), rows)
        assert result["protocol_deviations"] == [
            {"session_id": "s02", "deviation": "after_day_limit"},
        ]

    def test_an_unflagged_pilot_reports_no_deviations(self) -> None:
        assert summarize(make_manifest(), [make_session("s01")])["protocol_deviations"] == []


class TestCrossRecordAgreement:
    """Records have to agree with the manifest they were produced under."""

    def test_a_duplicate_session_id_is_refused(self) -> None:
        with pytest.raises(PilotError, match="recorded twice"):
            summarize(make_manifest(), [make_session("s01"), make_session("s01")])

    @pytest.mark.parametrize("field", ["pilot_id", "project_alias", "routine_revision"])
    def test_a_record_from_another_segment_is_refused(self, field: str) -> None:
        rows = [make_session("s01", **{field: "something-else"})]
        with pytest.raises(PilotError, match="disagrees with manifest.json"):
            summarize(make_manifest(), rows)

    def test_a_session_starting_before_the_pilot_is_refused(self) -> None:
        rows = [make_session("s01", started_at=at(hours=-1))]
        with pytest.raises(PilotError, match="starts before the pilot"):
            summarize(make_manifest(), rows)

    def test_an_unfilled_manifest_is_refused(self) -> None:
        """The published blank, unmodified. Six nulls, all named in one message."""
        blank = _template(MANIFEST_TEMPLATE)
        with pytest.raises(PilotError, match="is not filled in") as caught:
            summarize(blank, [])
        for field in ("client_version", "server_command", "database_path", "namespace",
                      "actor", "started_at"):
            assert field in str(caught.value), f"{field} was not named as unfilled"


class TestFieldValidation:
    """Shapes that would otherwise become a number nobody observed."""

    @pytest.mark.parametrize("field", ["search_calls", "get_calls", "capture_calls"])
    def test_a_boolean_is_not_a_count(self, field: str) -> None:
        """`True` is an int in Python. It would sum as 1 and read as a real observation."""
        with pytest.raises(PilotError, match="whole number of calls"):
            summarize(make_manifest(), [make_session("s01", **{field: True})])

    @pytest.mark.parametrize("field,value", [
        ("search_calls", -1), ("consult_seconds", -0.5), ("logging_seconds", float("inf")),
        ("consult_seconds", "12"), ("get_calls", 1.5),
    ])
    def test_impossible_measurements_are_refused(self, field, value) -> None:
        with pytest.raises(PilotError):
            summarize(make_manifest(), [make_session("s01", **{field: value})])

    @pytest.mark.parametrize("field,value", [
        ("state", "finished"), ("consultation", "yes"),
        ("task_outcome", "done"), ("impact", "positive"),
    ])
    def test_an_undocumented_enum_value_is_refused(self, field, value) -> None:
        with pytest.raises(PilotError, match="must be one of"):
            summarize(make_manifest(), [make_session("s01", **{field: value})])

    def test_an_undocumented_friction_code_is_refused(self) -> None:
        with pytest.raises(PilotError, match="accepts only"):
            summarize(make_manifest(), [make_session("s01", friction_codes=["slow"])])

    def test_a_repeated_friction_code_is_refused(self) -> None:
        rows = [make_session("s01", friction_codes=["scope", "scope"])]
        with pytest.raises(PilotError, match="counts once"):
            summarize(make_manifest(), rows)

    def test_a_naive_timestamp_is_refused(self) -> None:
        """Without an offset the deadline comparison would depend on an assumed zone."""
        rows = [make_session("s01", started_at="2026-09-23T09:00:00")]
        with pytest.raises(PilotError, match="UTC offset"):
            summarize(make_manifest(), rows)

    def test_a_session_with_no_start_is_refused(self) -> None:
        """The record is created when work begins, so this one is never legitimately unknown."""
        with pytest.raises(PilotError, match="started_at"):
            summarize(make_manifest(), [make_session("s01", started_at=None)])

    def test_an_unknown_field_is_refused_rather_than_ignored(self) -> None:
        with pytest.raises(PilotError, match="does not define"):
            summarize(make_manifest(), [make_session("s01", helpfulness_score=9)])

    def test_a_missing_field_is_refused(self) -> None:
        record = make_session("s01")
        del record["impact"]
        with pytest.raises(PilotError, match="missing required field"):
            summarize(make_manifest(), [record])

    def test_basis_checked_accepts_only_true_false_or_null(self) -> None:
        rows = [make_session("s01", reuse_evidence=[_reuse(capture_session_id=None,
                                                           basis_checked="probably")])]
        with pytest.raises(PilotError, match="basis_checked"):
            summarize(make_manifest(), rows)


class TestTheSummaryKeepsPrivateMaterialOut:
    """The output is still private, but it must not carry the parts that identify a host."""

    def test_no_host_scope_memory_id_or_prose_reaches_the_summary(self) -> None:
        rows = [
            make_session("s01", started_at=at(minutes=0), task_alias="secret-task-alias",
                         captured_memories=[_capture(memory_id="mem-private-id")],
                         note="a private note that must not be aggregated"),
            make_session("s02", started_at=at(hours=26),
                         reuse_evidence=[_reuse(memory_id="mem-private-id",
                                                action_note="a private action note")]),
        ]
        rendered = json.dumps(summarize(make_manifest(), rows))
        for leaked in ("pilot-test-namespace", "pilot-test-actor", "nexus-pilot-never-opened",
                       "mem-private-id", "secret-task-alias", "private note",
                       "private action note", sys.executable):
            assert leaked not in rendered, f"{leaked!r} reached the aggregate output"

    def test_the_summary_reports_the_documented_keys(self) -> None:
        result = summarize(make_manifest(), [make_session("s01")])
        assert list(result) == [
            "pilot_id", "routine_revision", "service_version", "recorded_sessions",
            "state_counts", "consultation_counts", "task_outcome_counts",
            "reported_impact_counts", "metric_coverage", "friction_session_counts",
            "evidence_coverage", "reuse_evidence_entries", "protocol_deviations",
        ]


class TestErrorsNameFieldsNotValues:
    """A diagnosis is read and pasted. It must not carry what the log is kept private for."""

    def test_a_refusal_does_not_echo_the_offending_value(self, tmp_path: Path) -> None:
        secret = "a-private-value-that-must-not-be-echoed"
        write_pilot(tmp_path, make_manifest(), [make_session("s01", state=secret)])
        stderr = refused(run_cli(tmp_path))
        assert secret not in stderr, "the diagnosis echoed the record's own value"
        assert "state" in stderr, "the diagnosis has to name the field to be actionable"
        assert "sessions/s01.json" in stderr, "the diagnosis has to name the file"


class TestCommandLine:
    """The script as it is actually invoked."""

    def test_an_empty_pilot_reports_successfully(self, tmp_path: Path) -> None:
        write_pilot(tmp_path, make_manifest(), [])
        report = accepted(run_cli(tmp_path))
        assert report["recorded_sessions"] == 0
        assert report["protocol_deviations"] == []

    def test_a_path_containing_spaces_is_handled(self, tmp_path: Path) -> None:
        root = tmp_path / "pilot logs" / "p1 first use"
        write_pilot(root, make_manifest(), [make_session("s01", search_calls=3)])
        report = accepted(run_cli(root))
        assert report["metric_coverage"]["search_calls"]["observed_sum"] == 3

    def test_an_all_unknown_pilot_reports_unknowns(self, tmp_path: Path) -> None:
        write_pilot(tmp_path, make_manifest(), [make_session("s01"), make_session("s02")])
        report = accepted(run_cli(tmp_path))
        assert report["recorded_sessions"] == 2
        assert report["metric_coverage"]["consult_seconds"]["observed_sum"] is None
        assert report["metric_coverage"]["consult_seconds"]["unknown_sessions"] == 2
        assert report["reuse_evidence_entries"]["observed_entries"] is None

    def test_a_valid_zero_count_survives_the_round_trip(self, tmp_path: Path) -> None:
        """Through JSON on disk and JSON on stdout, 0 must not become null or vanish."""
        write_pilot(tmp_path, make_manifest(),
                    [make_session("s01", capture_calls=0, friction_codes=[])])
        report = accepted(run_cli(tmp_path))
        assert report["metric_coverage"]["capture_calls"] == {
            "observed_sum": 0, "known_sessions": 1, "unknown_sessions": 0,
        }
        assert report["friction_session_counts"]["capture_effort"] == 0

    def test_an_over_limit_session_is_retained_with_a_deviation(self, tmp_path: Path) -> None:
        sessions = [make_session(f"s{n:02d}", started_at=at(hours=n)) for n in range(1, 12)]
        write_pilot(tmp_path, make_manifest(), sessions)
        report = accepted(run_cli(tmp_path))
        assert report["recorded_sessions"] == 11
        assert report["protocol_deviations"] == [
            {"session_id": "s11", "deviation": "over_session_limit"},
        ]

    def test_the_blank_template_manifest_is_refused(self, tmp_path: Path) -> None:
        write_pilot(tmp_path, _template(MANIFEST_TEMPLATE), [])
        assert "is not filled in" in refused(run_cli(tmp_path))

    def test_duplicate_session_ids_are_refused_naming_both_files(self, tmp_path: Path) -> None:
        """Two files, one id. Sorted order makes which is 'first' deterministic."""
        write_pilot(tmp_path, make_manifest(), [make_session("s01")])
        (tmp_path / "sessions" / "s01-retry.json").write_text(
            json.dumps(make_session("s01")), encoding="utf-8"
        )
        stderr = refused(run_cli(tmp_path))
        assert "sessions/s01-retry.json" in stderr and "sessions/s01.json" in stderr

    def test_truncated_json_is_refused(self, tmp_path: Path) -> None:
        write_pilot(tmp_path, make_manifest(), [make_session("s01")])
        path = tmp_path / "sessions" / "s01.json"
        path.write_text(path.read_text(encoding="utf-8")[:60], encoding="utf-8")
        stderr = refused(run_cli(tmp_path))
        assert "sessions/s01.json" in stderr and "not valid JSON" in stderr

    def test_a_duplicated_json_key_is_refused(self, tmp_path: Path) -> None:
        """json keeps the last of a repeated key, so this would otherwise parse cleanly."""
        write_pilot(tmp_path, make_manifest(), [make_session("s01")])
        path = tmp_path / "sessions" / "s01.json"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                '"impact": "unknown"', '"impact": "unknown",\n  "impact": "helped"', 1
            ),
            encoding="utf-8",
        )
        assert "duplicate object key" in refused(run_cli(tmp_path))

    def test_a_nonfinite_measurement_literal_is_refused(self, tmp_path: Path) -> None:
        """`NaN` is not JSON, but Python's parser accepts it unless told not to."""
        write_pilot(tmp_path, make_manifest(), [make_session("s01")])
        path = tmp_path / "sessions" / "s01.json"
        path.write_text(
            path.read_text(encoding="utf-8").replace('"consult_seconds": null',
                                                     '"consult_seconds": NaN', 1),
            encoding="utf-8",
        )
        assert "not valid JSON" in refused(run_cli(tmp_path))

    def test_a_changed_routine_revision_is_refused(self, tmp_path: Path) -> None:
        """A new routine is a new segment, not more rows for this one."""
        write_pilot(tmp_path, make_manifest(),
                    [make_session("s01"),
                     make_session("s02", routine_revision="0" * 40)])
        assert "disagrees with manifest.json" in refused(run_cli(tmp_path))

    def test_a_missing_linked_capture_session_is_refused(self, tmp_path: Path) -> None:
        write_pilot(tmp_path, make_manifest(),
                    [make_session("s02", reuse_evidence=[_reuse(capture_session_id="s01")])])
        assert "not in this log" in refused(run_cli(tmp_path))

    def test_a_missing_log_directory_is_refused(self, tmp_path: Path) -> None:
        assert "not a directory" in refused(run_cli(tmp_path / "nowhere"))

    def test_a_missing_sessions_directory_is_refused(self, tmp_path: Path) -> None:
        """Distinguishes 'no sessions yet' from 'the wrong --log-dir'."""
        (tmp_path / "manifest.json").write_text(json.dumps(make_manifest()), encoding="utf-8")
        assert "--log-dir" in refused(run_cli(tmp_path))

    def test_a_missing_manifest_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "sessions").mkdir()
        assert "manifest.json" in refused(run_cli(tmp_path))

    def test_the_log_dir_argument_is_required(self) -> None:
        """No default, so the script can never read a directory nobody named."""
        result = subprocess.run([sys.executable, str(SCRIPT)],
                                capture_output=True, text=True, timeout=TIMEOUT)
        assert result.returncode == 2 and result.stdout == ""


class TestTheReporterStaysOffline:
    """A stated constraint, checked rather than asserted in prose."""

    def test_the_configured_database_is_never_opened(self, tmp_path: Path) -> None:
        database = tmp_path / "memory.sqlite3"
        write_pilot(tmp_path, make_manifest(database_path=str(database)),
                    [make_session("s01", capture_calls=2)])
        accepted(run_cli(tmp_path))
        assert not database.exists(), (
            "the reporter created the database it was told about; it must only ever read "
            "the log directory it is given"
        )

    def test_the_script_imports_nothing_outside_the_standard_library(self) -> None:
        """No new runtime or development dependency may enter through this tool."""
        source = SCRIPT.read_text(encoding="utf-8")
        stdlib = {"argparse", "json", "math", "sys", "datetime", "pathlib", "__future__"}
        imported = {
            line.split()[1].split(".")[0]
            for line in source.splitlines()
            if line.startswith(("import ", "from "))
        }
        assert imported <= stdlib, f"unexpected import(s): {sorted(imported - stdlib)}"
