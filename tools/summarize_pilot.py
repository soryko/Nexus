#!/usr/bin/env python3
"""Read a private P1 pilot log and describe what is in it. Nothing more.

`docs/pilot-p1.md` asks a person to record one JSON file per programming session while
following the routine in `docs/daily-use.md`. This script is the reader for those files.
It exists for two reasons, and it is worth being clear that the second is the important one:

  * Arithmetic a person should not be doing by hand at the end of a fortnight.
  * A refusal. A record that is malformed, duplicated, or internally inconsistent stops the
    report instead of quietly vanishing from it -- because a log's failure mode is not a
    wrong total, it is a row that silently stopped being counted.

What the output establishes is **shape and arithmetic**: these records were well-formed,
and here is what they say. It is not evidence that the records are true, that memory helped,
or that the routine is worth following. This script therefore computes no benefit
percentage, no ROI, no token or money saving, no confidence interval, and no recommendation.

The distinction it works hardest to preserve is **unknown versus zero**. `null` in a record
means the participant did not count or did not review; `0` means they did, and it was zero.
A tool that folded the first into the second would turn an honest gap into a confident
measurement, so every numeric result carries its own coverage and a metric with no known
values reports `observed_sum: null`.

Offline and dependency-free by construction: standard library only, no MCP calls, no
subprocesses, no network, no SQLite, no credential discovery, no telemetry, no upload. It
reads the one directory it is given and writes to stdout.

Diagnostics name a **file and a field, never a value**. The log holds private paths, a
namespace, an actor, memory identifiers and free prose; an error message that echoed the
offending value would leak exactly the material the log is kept outside the repository to
protect. Three rules keep that true, and each of them replaces something that leaked:

  * A record is identified by the **file it came from**, or by its position when a caller
    supplies records directly -- never by its own `session_id`, which is input like any
    other field.
  * A **key** read out of a record is echoed only when it is on `DOCUMENTED_NAMES`.
    Duplicate-key detection runs before validation, so anything else is arbitrary content
    and is counted rather than named.
  * Enumerations are reported as the allowed set, which is public and lives in the template
    beside this script.

Refusal covers the whole path, not only the parsing of fields. Input that cannot be handled
exits 2 with a sanitized message rather than a traceback -- a traceback is not a refusal, it
prints the source line and the offending value and reads as a broken tool rather than
rejected input. That includes bytes that are not UTF-8, an integer too large to weigh, a
`day_limit` with no reachable deadline, and the case worth naming on its own: **valid inputs
whose total is not representable**. Individually valid measurements can sum to infinity, can
make `sum()` raise part-way through on an integer too large to convert, or can produce an
exact integer no float can weigh -- see `observed_metric`. The aggregate is checked for all
three, and the report is serialized in full to a string before a byte reaches stdout, so a
late failure cannot leave a partial document behind.

    python3 tools/summarize_pilot.py --log-dir "$pilot_log_dir"

Exit 0 means the inputs were structurally valid -- including an empty or wholly incomplete
pilot, which is a legitimate thing to have. Exit 2 means they were not, and nothing is
written to stdout in that case.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 1

# The enumerations `docs/pilot-p1.md` defines. Kept as tuples so the report's key order is
# the documented order rather than whatever a set iterates in.
STATES = ("open", "completed", "abandoned")
CONSULTATIONS = ("attempted", "not_attempted", "unknown")
TASK_OUTCOMES = ("completed", "partial", "failed", "abandoned", "unknown")
IMPACTS = ("helped", "neutral", "harmed", "not_used", "unknown")
FRICTION_CODES = (
    "connection", "scope", "capture_effort", "no_relevant_hit", "irrelevant_hit",
    "stale_memory", "verification_effort", "correction_conflict", "consultation_overhead",
    "logging_burden", "other",
)

COUNT_FIELDS = ("search_calls", "get_calls", "capture_calls")
DURATION_FIELDS = ("consult_seconds", "logging_seconds")
METRIC_FIELDS = COUNT_FIELDS + DURATION_FIELDS
EVIDENCE_FIELDS = ("captured_memories", "reuse_evidence", "friction_codes")

# Every manifest field has to be filled before a report means anything: each one is part of
# the provenance the rows are being read against. The blank template ships with six of them
# null on purpose, and this is what refuses it.
MANIFEST_TEXT_FIELDS = (
    "pilot_id", "routine_revision", "service_version", "client_name", "client_version",
    "project_alias", "server_command", "database_path", "namespace", "actor",
)
MANIFEST_FIELDS = frozenset(
    MANIFEST_TEXT_FIELDS + ("schema_version", "started_at", "session_limit", "day_limit")
)
SESSION_FIELDS = frozenset(
    ("schema_version", "pilot_id", "session_id", "task_alias", "project_alias",
     "routine_revision", "started_at", "state", "consultation", "task_outcome", "impact",
     "captured_memories", "reuse_evidence", "friction_codes", "note") + METRIC_FIELDS
)

CAPTURE_ENTRY_FIELDS = frozenset(("memory_id", "revision_id"))
REUSE_ENTRY_FIELDS = frozenset(
    ("memory_id", "revision_id", "capture_session_id", "basis_checked", "action_note")
)

# The only names a diagnostic may ever repeat back. A key read out of an input file is not
# necessarily one of these -- a malformed record can carry any key at all, and echoing one
# would put arbitrary file content into a message that gets pasted elsewhere. So a name is
# echoed when it is on this list and counted when it is not.
DOCUMENTED_NAMES = frozenset(
    MANIFEST_FIELDS | SESSION_FIELDS | CAPTURE_ENTRY_FIELDS | REUSE_ENTRY_FIELDS
)


class PilotError(Exception):
    """An input this tool refuses to summarize. Carries a location, never a value."""


def _fail(location: str, problem: str) -> None:
    raise PilotError(f"{location}: {problem}")


# --------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------

def _reject_constant(name: str):
    # json accepts NaN/Infinity/-Infinity by default. A duration of NaN would compare false
    # against every bound and sum to NaN, poisoning a total that still looks like a number.
    raise ValueError(f"{name} is not permitted")


def _reject_duplicate_keys(pairs):
    # Python's default keeps the last of a repeated key, so `{"impact":"helped",
    # "impact":"harmed"}` would parse cleanly as the second one. This runs BEFORE any
    # validation, so the key is arbitrary file content until it is shown to be on the
    # allowlist -- an earlier version named it unconditionally, which leaked.
    seen: set[str] = set()
    for key, _value in pairs:
        if key in seen:
            named = repr(key) if key in DOCUMENTED_NAMES else "an undocumented key, not echoed"
            raise ValueError(f"duplicate object key: {named}")
        seen.add(key)
    return dict(pairs)


def _read_json(path: Path, location: str) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Deliberately says nothing about the offending bytes, and nothing about where.
        _fail(location, "is not valid UTF-8 text")
    except OSError as exc:
        _fail(location, f"cannot be read ({exc.strerror or exc.__class__.__name__})")
    try:
        parsed = json.loads(
            text, parse_constant=_reject_constant, object_pairs_hook=_reject_duplicate_keys
        )
    except ValueError as exc:
        _fail(location, f"is not valid JSON ({exc})")
    if not isinstance(parsed, dict):
        _fail(location, "is not a JSON object")
    return parsed


def _session_files(root: Path) -> list[Path]:
    """The session records, in the order they are read. One definition, used twice."""
    return sorted((root / "sessions").glob("*.json"))


def load_pilot(root: Path) -> tuple[dict, list[dict]]:
    """Read and structurally validate `manifest.json` and `sessions/*.json` under `root`.

    Per-record validation only; checks that need the whole set -- unique identifiers,
    agreement with the manifest, capture links -- belong to `summarize`, so that a caller
    holding records in memory gets them too.
    """
    root = Path(root)
    if not root.is_dir():
        _fail(str(root), "is not a directory")

    manifest = _validate_manifest(_read_json(root / "manifest.json", "manifest.json"))

    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        _fail(
            "sessions/",
            "is missing -- an empty sessions directory is a pilot with no sessions yet, "
            "but a missing one usually means the wrong --log-dir",
        )

    sessions: list[dict] = []
    seen: dict[str, str] = {}
    for path in _session_files(root):
        location = f"sessions/{path.name}"
        record = _validate_session(_read_json(path, location), location)
        # Caught here as well as in `summarize` because only here are both filenames known,
        # and "which two files" is the whole of what makes this one actionable.
        first = seen.get(record["session_id"])
        if first is not None:
            _fail(location, f"repeats the session_id already recorded in {first}")
        seen[record["session_id"]] = location
        sessions.append(record)
    return manifest, sessions


# --------------------------------------------------------------------------------------
# Field validation
# --------------------------------------------------------------------------------------

def _exact_keys(record: dict, expected: frozenset[str], location: str) -> None:
    present = set(record)
    missing = sorted(expected - present)
    unknown = sorted(present - expected)
    if missing:
        _fail(location, f"is missing required field(s): {', '.join(missing)}")
    if unknown:
        # A key that is documented somewhere else in this format is safe to name -- it is a
        # field in the wrong file, which is the useful thing to say. Anything else is
        # arbitrary content from the record and is only counted.
        nameable = [key for key in unknown if key in DOCUMENTED_NAMES]
        opaque = len(unknown) - len(nameable)
        parts = []
        if nameable:
            parts.append(f"misplaced here: {', '.join(nameable)}")
        if opaque:
            parts.append(f"{opaque} undocumented key(s), not echoed")
        _fail(location, f"carries field(s) this format does not define -- {'; '.join(parts)}")


def _schema_version(record: dict, location: str) -> None:
    value = record["schema_version"]
    if isinstance(value, bool) or value != SCHEMA_VERSION:
        _fail(location, f"field 'schema_version' must be exactly {SCHEMA_VERSION}")


def _text(record: dict, field: str, location: str) -> str:
    value = record[field]
    if not isinstance(value, str) or not value.strip():
        _fail(location, f"field {field!r} must be a non-empty string")
    return value


def _enum(record: dict, field: str, allowed: tuple[str, ...], location: str) -> str:
    value = record[field]
    if not isinstance(value, str) or value not in allowed:
        _fail(location, f"field {field!r} must be one of: {', '.join(allowed)}")
    return value


def _timestamp(raw, field: str, location: str) -> datetime:
    if not isinstance(raw, str):
        _fail(location, f"field {field!r} must be an ISO 8601 timestamp string")
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        _fail(location, f"field {field!r} is not a parseable ISO 8601 timestamp")
    if moment.tzinfo is None or moment.utcoffset() is None:
        # A naive timestamp cannot be ordered against the manifest's start without assuming
        # a zone, and assuming one is how a session silently moves across the deadline.
        _fail(location, f"field {field!r} must carry a UTC offset; a naive timestamp is ambiguous")
    return moment


def _count(record: dict, field: str, location: str) -> int | None:
    value = record[field]
    if value is None:
        return None
    # bool is a subclass of int, so `True` would otherwise pass as the count 1.
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(location, f"field {field!r} must be a whole number of calls, or null if not counted")
    if value < 0:
        _fail(location, f"field {field!r} cannot be negative")
    return value


def _duration(record: dict, field: str, location: str) -> float | None:
    value = record[field]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(location, f"field {field!r} must be a number of seconds, or null if not measured")
    # parse_constant catches the NaN/Infinity literals; a float overflowing from something
    # like 1e400 arrives here as inf without ever passing through it. An integer literal of
    # a few hundred digits reaches math.isfinite and raises OverflowError converting to
    # float, which is an input problem and has to read as one.
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _fail(location, f"field {field!r} must be a finite number of seconds")
    if value < 0:
        _fail(location, f"field {field!r} cannot be negative")
    return value


def _optional_text(record: dict, field: str, location: str) -> str | None:
    value = record[field]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(location, f"field {field!r} must be a non-empty string, or null")
    return value


def _entry_list(value, field: str, entry_fields: frozenset[str], location: str):
    """`null` (unknown), `[]` (reviewed, none), or a list of objects with exactly these keys."""
    if value is None:
        return None
    if not isinstance(value, list):
        _fail(location, f"field {field!r} must be null if unreviewed, or a list of entries")
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            _fail(location, f"field {field!r} entry {index} is not an object")
        _exact_keys(entry, entry_fields, f"{location} field {field!r} entry {index}")
    return value


def _validate_manifest(record: dict) -> dict:
    location = "manifest.json"
    _exact_keys(record, MANIFEST_FIELDS, location)
    _schema_version(record, location)

    # Collected rather than reported one at a time: the published blank ships with six nulls
    # and filling them one error at a time would be six runs of this script.
    unfilled = [f for f in MANIFEST_TEXT_FIELDS + ("started_at",) if record[f] is None]
    if unfilled:
        _fail(
            location,
            "is not filled in. Complete these field(s) from the client entry that launches "
            f"the server before reporting: {', '.join(unfilled)}",
        )

    for field in MANIFEST_TEXT_FIELDS:
        _text(record, field, location)
    _timestamp(record["started_at"], "started_at", location)
    for field in ("session_limit", "day_limit"):
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            _fail(location, f"field {field!r} must be a positive whole number")
    return record


def _validate_session(record: dict, location: str) -> dict:
    _exact_keys(record, SESSION_FIELDS, location)
    _schema_version(record, location)

    for field in ("pilot_id", "session_id", "project_alias", "routine_revision"):
        _text(record, field, location)
    _optional_text(record, "task_alias", location)
    if record["started_at"] is None:
        _fail(
            location,
            "field 'started_at' is null. The record is created when the work begins, so a "
            "start time is always known, even when nothing else about the session is",
        )
    _timestamp(record["started_at"], "started_at", location)

    _enum(record, "state", STATES, location)
    _enum(record, "consultation", CONSULTATIONS, location)
    _enum(record, "task_outcome", TASK_OUTCOMES, location)
    _enum(record, "impact", IMPACTS, location)

    for field in COUNT_FIELDS:
        _count(record, field, location)
    for field in DURATION_FIELDS:
        _duration(record, field, location)

    captured = _entry_list(
        record["captured_memories"], "captured_memories", CAPTURE_ENTRY_FIELDS, location
    )
    for index, entry in enumerate(captured or []):
        where = f"{location} field 'captured_memories' entry {index}"
        for field in CAPTURE_ENTRY_FIELDS:
            _text(entry, field, where)

    reuse = _entry_list(record["reuse_evidence"], "reuse_evidence", REUSE_ENTRY_FIELDS, location)
    for index, entry in enumerate(reuse or []):
        where = f"{location} field 'reuse_evidence' entry {index}"
        for field in ("memory_id", "revision_id", "action_note"):
            _text(entry, field, where)
        _optional_text(entry, "capture_session_id", where)
        # `x not in (True, False, None)` compares by equality, under which 1 == True and
        # 0 == False, so every numeric 0/1 and 0.0/1.0 passed that test. The documented type
        # is boolean-or-null and this is what actually requires it.
        basis = entry["basis_checked"]
        if basis is not None and not isinstance(basis, bool):
            _fail(where, "field 'basis_checked' must be true, false, or null")

    friction = record["friction_codes"]
    if friction is not None:
        if not isinstance(friction, list):
            _fail(location, "field 'friction_codes' must be null if unreviewed, or a list of codes")
        seen: set[str] = set()
        for code in friction:
            if not isinstance(code, str) or code not in FRICTION_CODES:
                _fail(location, f"field 'friction_codes' accepts only: {', '.join(FRICTION_CODES)}")
            if code in seen:
                _fail(location, f"field 'friction_codes' repeats {code!r}; each code counts once")
            seen.add(code)

    if not isinstance(record["note"], str):
        _fail(location, "field 'note' must be a string, empty if there is nothing to add")
    return record


# --------------------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------------------

def observed_metric(rows: list[dict], field: str) -> dict:
    """A sum of what was observed, beside how much was not.

    `observed_sum` is null when nothing was counted, so no reader can mistake an empty
    pilot, or an unmeasured one, for a measured zero.

    The total is checked as well as the parts, because individually valid values need not
    have a representable sum. There are three ways out of range and they do not look alike:

      * Two durations of 1e308 sum to `inf`, which was reported as the literal `Infinity`
        -- which this same script refuses on input.
      * Two integers of 10**308 and one float make `sum()` itself raise `OverflowError`
        converting its accumulated integer to float, part-way through.
      * The same two integers without the float sum to an exact integer that no float can
        hold, so `isfinite` raises rather than returning False. An earlier guard tested
        `isinstance(total, float)` and this walked straight past it, printing a 309-digit
        integer at exit 0.

    One check covers all three: attempt the sum, attempt to weigh it, and treat a refusal
    from either as out of range.
    """
    values = [row[field] for row in rows if row[field] is not None]
    total: int | float | None = None
    representable = True
    try:
        if values:
            total = sum(values)
            representable = math.isfinite(total)
    except OverflowError:
        representable = False
    if not representable:
        # Deliberately neutral. Every record that fed this was individually valid, and the
        # tool has no basis for saying which of them is the wrong one -- or that any single
        # one is, rather than the set being larger than this report can add up.
        _fail(
            "metric_coverage",
            f"the recorded values for {field!r} sum beyond the numeric range this report "
            f"can represent. Each record was individually valid, so this does not establish "
            f"which of them is wrong",
        )
    return {
        "observed_sum": total,
        "known_sessions": len(values),
        "unknown_sessions": len(rows) - len(values),
    }


def _tally(rows: list[dict], field: str, allowed: tuple[str, ...]) -> dict:
    counts = dict.fromkeys(allowed, 0)
    for row in rows:
        counts[row[field]] += 1
    return counts


def _check_links(rows: list[dict], starts: dict[str, datetime], labels: list[str]) -> None:
    """A claimed capture link has to survive being looked up. It is not proof of anything.

    All this establishes is that the cited source session exists, is a different and earlier
    one, and recorded the memory being cited. Whether the memory was genuinely reused is a
    judgement `docs/pilot-p1.md` reserves for a person reading the records.
    """
    by_id = {row["session_id"]: row for row in rows}
    for where, row in zip(labels, rows):
        for index, entry in enumerate(row["reuse_evidence"] or []):
            source_id = entry["capture_session_id"]
            if source_id is None:
                continue                       # unlinked, and reported as such
            at = f"{where} field 'reuse_evidence' entry {index}"
            source = by_id.get(source_id)
            if source is None:
                _fail(at, "names a capture session that is not in this log")
            if source_id == row["session_id"]:
                _fail(at, "names its own session; same-session capture is not cross-session reuse")
            if starts[source_id] >= starts[row["session_id"]]:
                _fail(at, "names a capture session that did not start earlier than this one")
            captured = source["captured_memories"]
            if captured is None:
                _fail(
                    at,
                    "names a capture session that records no captured memories, so the link "
                    "cannot be checked; record capture_session_id as null if the source of "
                    "the memory is not established",
                )
            # Revisions deliberately not compared: a memory may have been revised between
            # being captured and being reused, and that is the normal case, not a defect.
            if not any(item["memory_id"] == entry["memory_id"] for item in captured):
                _fail(at, "cites a memory the named capture session does not record capturing")


def summarize(manifest: dict, sessions: list[dict], locations: list[str] | None = None) -> dict:
    """Describe the records. Cross-record validation happens here, then counting.

    `locations` labels each record for diagnostics -- the CLI passes filenames. A record is
    never identified by its own `session_id`, because that value is arbitrary input and an
    earlier version put it straight into stderr.
    """
    if not isinstance(manifest, dict):
        _fail("manifest.json", "is not a JSON object")
    _validate_manifest(manifest)
    pilot_start = _timestamp(manifest["started_at"], "started_at", "manifest.json")
    try:
        deadline = pilot_start + timedelta(days=manifest["day_limit"])
    except (OverflowError, OSError, ValueError):
        # A day_limit large enough to push the deadline past the calendar. Absurd, but it
        # arrives from a file, so it refuses like any other bad field.
        _fail("manifest.json", "field 'day_limit' is too large to produce a deadline")

    labels = (
        list(locations)
        if locations is not None and len(locations) == len(sessions)
        else [f"session record {n}" for n in range(1, len(sessions) + 1)]
    )

    starts: dict[str, datetime] = {}
    for where, row in zip(labels, sessions):
        if not isinstance(row, dict):
            _fail(where, "is not an object")
        _validate_session(row, where)
        if row["session_id"] in starts:
            _fail(where, "is recorded twice")
        for field in ("pilot_id", "project_alias", "routine_revision"):
            if row[field] != manifest[field]:
                # Not reconciled into the manifest's value: a routine change means the rows
                # were produced under different instructions and aggregating across the
                # break would average two things that are not the same measurement.
                _fail(
                    where,
                    f"field {field!r} disagrees with manifest.json. A change here starts a "
                    f"separately versioned segment; it is not aggregated with this one",
                )
        moment = _timestamp(row["started_at"], "started_at", where)
        if moment < pilot_start:
            _fail(where, "starts before the pilot's own start time in manifest.json")
        starts[row["session_id"]] = moment

    _check_links(sessions, starts, labels)

    # Deterministic, and in the order the sessions actually happened.
    ordered = sorted(sessions, key=lambda row: (starts[row["session_id"]], row["session_id"]))

    deviations: list[dict] = []
    for rank, row in enumerate(ordered):
        if rank >= manifest["session_limit"]:
            deviations.append({"session_id": row["session_id"], "deviation": "over_session_limit"})
        if starts[row["session_id"]] >= deadline:
            deviations.append({"session_id": row["session_id"], "deviation": "after_day_limit"})

    return {
        "pilot_id": manifest["pilot_id"],
        "routine_revision": manifest["routine_revision"],
        "service_version": manifest["service_version"],
        "recorded_sessions": len(ordered),
        "state_counts": _tally(ordered, "state", STATES),
        "consultation_counts": _tally(ordered, "consultation", CONSULTATIONS),
        "task_outcome_counts": _tally(ordered, "task_outcome", TASK_OUTCOMES),
        "reported_impact_counts": _tally(ordered, "impact", IMPACTS),
        "metric_coverage": {f: observed_metric(ordered, f) for f in METRIC_FIELDS},
        "friction_session_counts": _friction_counts(ordered),
        "evidence_coverage": {
            field: {
                "known_sessions": sum(1 for r in ordered if r[field] is not None),
                "unknown_sessions": sum(1 for r in ordered if r[field] is None),
            }
            for field in EVIDENCE_FIELDS
        },
        "reuse_evidence_entries": _reuse_counts(ordered),
        "protocol_deviations": deviations,
    }


def _friction_counts(rows: list[dict]) -> dict:
    """Occurrences per code -- or all null, when no session's list was reviewed at all.

    Every documented code appears either way, so a code with no occurrences reads as the
    observed zero it is rather than being absent and looking like an oversight.
    """
    reviewed = [row["friction_codes"] for row in rows if row["friction_codes"] is not None]
    if not reviewed:
        return dict.fromkeys(FRICTION_CODES, None)
    counts = dict.fromkeys(FRICTION_CODES, 0)
    for codes in reviewed:
        for code in codes:
            counts[code] += 1
    return counts


def _reuse_counts(rows: list[dict]) -> dict:
    """Entries recorded, split by whether their capture session was named and resolved."""
    reviewed = [row["reuse_evidence"] for row in rows if row["reuse_evidence"] is not None]
    if not reviewed:
        return {"observed_entries": None, "linked_entries": None, "unlinked_entries": None}
    entries = [entry for lst in reviewed for entry in lst]
    linked = sum(1 for entry in entries if entry["capture_session_id"] is not None)
    return {
        "observed_entries": len(entries),
        "linked_entries": linked,
        "unlinked_entries": len(entries) - linked,
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="summarize_pilot.py",
        description="Describe a private P1 pilot log. Reads only the directory it is given.",
    )
    parser.add_argument(
        "--log-dir", required=True, type=Path,
        help="the private pilot directory holding manifest.json and sessions/",
    )
    args = parser.parse_args(argv)

    try:
        manifest, sessions = load_pilot(args.log_dir)
        report = summarize(
            manifest, sessions,
            [f"sessions/{path.name}" for path in _session_files(args.log_dir)],
        )
        # Serialized in full, to a string, before a byte is printed. Two separate reasons:
        # a failure late in aggregation cannot leave half a report on stdout, and
        # `json.dump` streams -- so allow_nan=False raising mid-write would emit a partial
        # document and then fail. Nothing reaches stdout until the whole thing exists.
        rendered = json.dumps(report, indent=2, allow_nan=False)
    except PilotError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError:
        # Belt and braces behind observed_metric's own check: whatever produced a value
        # JSON cannot represent, the report is not written.
        print("the summary contains a value that cannot be represented in JSON",
              file=sys.stderr)
        return 2

    sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
