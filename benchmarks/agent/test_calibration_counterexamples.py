"""Counterexamples for the calibration driver, its accounting, and the environment gate.

No model, no network, no fixtures. Every case below is one that was WRONG in a published
revision, and each is the kind of wrong that is silent: a budget that stops counting, a gate
that passes a failing test run, a summary that crashes only on the path nobody exercised.

    python3 test_calibration_counterexamples.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import a1_config                                                        # noqa: E402
import identity as ident                                                # noqa: E402
import run_calibration as RC                                            # noqa: E402
import verify_arm_environment as V                                      # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILS.append(f"{name}: {detail}")


def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


# --------------------------------------------------------------------------- accounting
def test_missing_envelope_is_unresolved_not_zero():
    """A trace with no terminal envelope is consumption of UNKNOWN size, never of zero size."""
    d = _tmp()
    arm = d / "c30" / "run-k1" / "attempt1" / "arms" / "baseline"
    arm.mkdir(parents=True)
    (arm / "trace.jsonl").write_text('{"type":"assistant","message":{"content":[]}}\n')
    c = RC.consumed(d)
    check("trace without envelope -> unresolved", c["unresolved"] == 1, json.dumps(c))
    check("trace without envelope -> counted as a run", c["arm_runs"] == 1, json.dumps(c))


def test_record_without_usage_is_unresolved_not_zero():
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    at.mkdir(parents=True)
    (at / "records.json").write_text(json.dumps({"records": [{"arm": "baseline"}]}))
    c = RC.consumed(d)
    check("record without usage -> unresolved", c["unresolved"] == 1, json.dumps(c))


def test_per_arm_record_counts_when_row_never_completed():
    """Two arms finish, the row dies before records.json. Their tokens are still spent."""
    d = _tmp()
    arm = d / "c30" / "run-k1" / "attempt1" / "arms" / "baseline"
    arm.mkdir(parents=True)
    (arm / "record.json").write_text(json.dumps(
        {"arm": "baseline", "record": {"usage": {"input_tokens": 10, "output_tokens": 5}}}))
    c = RC.consumed(d)
    check("per-arm record counted", c["tokens_known"] == 15 and c["unresolved"] == 0,
          json.dumps(c))


def test_quarantine_by_rename_does_not_drop_consumption():
    """The glob must not depend on a directory's name -- renaming once dropped 6.4M tokens."""
    d = _tmp()
    at = d / "v1-c30-quarantined" / "run-k1" / "attempt1"
    at.mkdir(parents=True)
    (at / "records.json").write_text(json.dumps(
        {"records": [{"arm": "baseline", "usage": {"input_tokens": 100}}]}))
    c = RC.consumed(d)
    check("renamed directory still counted", c["tokens_known"] == 100, json.dumps(c))


def test_arm_counted_once_across_sources():
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    (at / "arms" / "baseline").mkdir(parents=True)
    (at / "arms" / "baseline" / "record.json").write_text(json.dumps(
        {"arm": "baseline", "record": {"usage": {"input_tokens": 7}}}))
    (at / "records.json").write_text(json.dumps(
        {"records": [{"arm": "baseline", "usage": {"input_tokens": 7}}]}))
    c = RC.consumed(d)
    check("no double count", c["tokens_known"] == 7 and c["arm_runs"] == 1, json.dumps(c))


# ------------------------------------------------------------------- allowance accounting
def _allowance(arm_dir: Path, tokens, **over) -> None:
    arm_dir.mkdir(parents=True, exist_ok=True)
    data = {"kind": "conservative_allowance", "tokens_allowance": tokens,
            "task": arm_dir.parents[2].name.replace("run-", "", 1),
            "attempt": int(arm_dir.parents[1].name.replace("attempt", "", 1)),
            "arm": arm_dir.name, "basis": "above the largest completed v1 arm-run"}
    data.update(over)
    (arm_dir / "resolution.json").write_text(json.dumps(data))


def _arm(d: Path, task="k1", arm="baseline") -> Path:
    a = d / "c30" / f"run-{task}" / "attempt1" / "arms" / arm
    a.mkdir(parents=True, exist_ok=True)
    return a


def test_recovered_usage_supersedes_an_allowance_whatever_source_it_came_from():
    """One arm-run, one charge -- from every source the usage can be recovered through.

    The allowance was applied FIRST and then the sources were treated inconsistently: a
    `record.json` was counted again on top of it (3.4M charged, two runs, for one arm-run that
    spent 2M), while a `records.json` row or a trace was skipped as already-seen and its
    recovered measurement silently DISCARDED (1.4M charged for the same 2M spend). The same
    recovered consumption gave three different budget answers.
    """
    usage = {"input_tokens": 2_000_000}
    for label, place in (
        ("per-arm record", lambda a: (a / "record.json").write_text(
            json.dumps({"arm": a.name, "record": {"usage": usage}}))),
        ("row record", lambda a: (a.parents[1] / "records.json").write_text(
            json.dumps({"records": [{"arm": a.name, "usage": usage}]}))),
        ("trace envelope", lambda a: (a / "trace.jsonl").write_text(
            json.dumps({"type": "result", "usage": usage}) + "\n")),
    ):
        d = _tmp()
        a = _arm(d)
        _allowance(a, 1_400_000)
        place(a)
        c = RC.consumed(d)
        check(f"{label}: one arm-run", c["arm_runs"] == 1, json.dumps(c))
        check(f"{label}: measurement is the charge", c["tokens_budgeted"] == 2_000_000,
              json.dumps(c))
        check(f"{label}: measurement not lost", c["tokens_known"] == 2_000_000, json.dumps(c))
        check(f"{label}: allowance not added", c["tokens_allowance"] == 0, json.dumps(c))
        check(f"{label}: superseded allowance kept for audit",
              c["allowance_superseded_arm_runs"] == [str(a)], json.dumps(c))


def test_allowance_applies_only_where_nothing_was_recovered():
    d = _tmp()
    a = _arm(d)
    (a / "launched.json").write_text("{}")
    _allowance(a, 1_400_000)
    c = RC.consumed(d)
    check("allowance resolves the blocker", c["unresolved"] == 0, json.dumps(c))
    check("allowance is the charge", c["tokens_budgeted"] == 1_400_000, json.dumps(c))
    check("allowance is not a measurement", c["tokens_known"] == 0, json.dumps(c))
    check("one arm-run", c["arm_runs"] == 1, json.dumps(c))


def test_an_allowance_does_not_make_consumption_certain():
    """It cleared `unresolved`, and the summary read that as certainty about SPEND. It
    establishes a budget charge; the registration's own upper-bound claim is an argument about
    the killed run, not a measurement of it."""
    d = _tmp()
    a = _arm(d)
    (a / "launched.json").write_text("{}")
    _allowance(a, 1_400_000)
    check("allowance -> consumption not certain", RC.consumed(d)["consumption_certain"] is False)
    d2 = _tmp()
    (_arm(d2) / "record.json").write_text(json.dumps({"record": {"usage": {"input_tokens": 5}}}))
    check("measurement only -> consumption certain",
          RC.consumed(d2)["consumption_certain"] is True)


def test_an_allowance_survives_preserving_its_interrupted_attempt():
    """`preserve_partial` renames `attempt1` to `attempt1.partial-<timestamp>`, and the
    allowance validator read that whole suffix as part of the attempt NUMBER -- so a correctly
    filed allowance was refused the moment its attempt was preserved. Preservation runs before
    accounting in the driver, so this is the ordinary path, not a corner: a valid reconciliation
    could not unblock an interrupted sweep."""
    d = _tmp()
    a = _arm(d)
    (a / "launched.json").write_text("{}")
    _allowance(a, 1_400_000)
    before = RC.consumed(d)
    check("before preservation: charged once", before["tokens_allowance"] == 1_400_000,
          json.dumps(before))
    moved = RC.preserve_partial(a.parents[1])
    check("attempt was preserved", moved is not None and ".partial-" in moved.name, str(moved))
    after = RC.consumed(d)
    check("after preservation: still charged once", after["tokens_allowance"] == 1_400_000,
          json.dumps(after))
    check("after preservation: still one arm-run", after["arm_runs"] == 1, json.dumps(after))
    check("after preservation: nothing outstanding", after["unresolved"] == 0, json.dumps(after))
    check("after preservation: nothing refused", after["allowance_rejected"] == [],
          json.dumps(after))


def test_preservation_does_not_weaken_the_allowance_identity_check():
    """The suffix is tolerated; the identity inside it is not. Refusing after a rename was the
    defect, but accepting anything after a rename would be a worse one."""
    for label, over in (("wrong arm", dict(arm="nexus")), ("wrong task", dict(task="k9")),
                        ("wrong attempt", dict(attempt=7))):
        d = _tmp()
        a = _arm(d)
        (a / "launched.json").write_text("{}")
        _allowance(a, 1_400_000, **over)
        RC.preserve_partial(a.parents[1])
        c = RC.consumed(d)
        check(f"{label} still refused after preservation", c["unresolved"] == 1, json.dumps(c))
        check(f"{label} charges nothing", c["tokens_allowance"] == 0, json.dumps(c))


def test_recovered_usage_supersedes_an_allowance_after_preservation():
    """The preserved path is still an arm-run, so a measurement recovered there still wins."""
    d = _tmp()
    a = _arm(d)
    _allowance(a, 1_400_000)
    (a / "record.json").write_text(json.dumps({"record": {"usage": {"input_tokens": 2_000_000}}}))
    RC.preserve_partial(a.parents[1])
    c = RC.consumed(d)
    check("measurement wins after preservation",
          c["tokens_budgeted"] == 2_000_000 and c["tokens_allowance"] == 0, json.dumps(c))
    check("one arm-run", c["arm_runs"] == 1, json.dumps(c))


def test_a_directory_that_is_not_an_attempt_refuses():
    """Validating the format is what makes tolerating the suffix safe."""
    d = _tmp()
    a = d / "c30" / "run-k1" / "attemptZZ" / "arms" / "baseline"
    a.mkdir(parents=True)
    (a / "launched.json").write_text("{}")
    (a / "resolution.json").write_text(json.dumps(
        {"kind": "conservative_allowance", "task": "k1", "attempt": 1, "arm": "baseline",
         "tokens_allowance": 10, "basis": "b"}))
    c = RC.consumed(d)
    check("not an attempt directory -> refused", c["unresolved"] == 1, json.dumps(c))
    check("and says so", any("attempt directory" in w for w in c["allowance_rejected"]),
          json.dumps(c["allowance_rejected"]))


def test_a_refused_allowance_leaves_the_arm_run_outstanding():
    """Even with no other evidence in the directory. A refused allowance is still somebody's
    statement that an arm-run happened there; dropping it made the accounting look complete."""
    for label, kwargs in (("negative", dict(tokens=-100)),
                          ("unrecognised kind", dict(tokens=10, kind="note_to_self"))):
        d = _tmp()
        _allowance(_arm(d), kwargs.pop("tokens"), **kwargs)
        c = RC.consumed(d)
        check(f"{label}: arm-run still outstanding", c["unresolved"] == 1, json.dumps(c))
        check(f"{label}: counted as a run", c["arm_runs"] == 1, json.dumps(c))
        check(f"{label}: consumption not certain", c["consumption_certain"] is False,
              json.dumps(c))


def test_an_unusable_allowance_does_not_clear_the_blocker():
    for label, kwargs in (
        ("negative", dict(tokens=-100)),
        ("boolean", dict(tokens=True)),
        ("fractional", dict(tokens=1.5)),
        ("string", dict(tokens="1400000")),
        ("filed against another arm", dict(tokens=10, arm="nexus")),
        ("filed against another task", dict(tokens=10, task="k9")),
        ("filed against another attempt", dict(tokens=10, attempt=7)),
        ("no basis", dict(tokens=10, basis="   ")),
    ):
        d = _tmp()
        a = _arm(d)
        (a / "launched.json").write_text("{}")
        _allowance(a, kwargs.pop("tokens"), **kwargs)
        c = RC.consumed(d)
        check(f"{label} allowance refused", c["unresolved"] == 1, json.dumps(c))
        check(f"{label} allowance charges nothing", c["tokens_budgeted"] == 0, json.dumps(c))
        check(f"{label} allowance says why", len(c["allowance_rejected"]) == 1, json.dumps(c))


# ------------------------------------------------------- the runner's missing-usage record
# What run_arms_isolated actually writes when the envelope carried no usage: NOT an absent key
# and NOT an empty dict, but four keys whose values are null. The emptiness test admitted it
# and the sum then raised TypeError on the only path that writes a summary.
RUNNER_MISSING_USAGE = {k: None for k in
                        ("input_tokens", "output_tokens", "cache_read_input_tokens",
                         "cache_creation_input_tokens")}


def test_runners_missing_usage_record_is_unresolved_not_a_crash():
    for label, place in (
        ("per-arm record", lambda d, a: (a / "record.json").write_text(
            json.dumps({"arm": a.name, "record": {"usage": RUNNER_MISSING_USAGE}}))),
        ("row record", lambda d, a: (a.parents[1] / "records.json").write_text(
            json.dumps({"records": [{"arm": a.name, "usage": RUNNER_MISSING_USAGE}]}))),
        ("trace envelope", lambda d, a: (a / "trace.jsonl").write_text(
            json.dumps({"type": "result", "usage": RUNNER_MISSING_USAGE}) + "\n")),
    ):
        d = _tmp()
        place(d, _arm(d))
        c = RC.consumed(d)
        check(f"{label}: unresolved, not zero", c["unresolved"] == 1, json.dumps(c))
        check(f"{label}: counted as a run", c["arm_runs"] == 1, json.dumps(c))
        check(f"{label}: contributes no tokens", c["tokens_known"] == 0, json.dumps(c))


def test_row_reserve_survives_the_runners_missing_usage_record():
    """The same shape crashed the reserve calculation, which runs BEFORE a row is started."""
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    at.mkdir(parents=True)
    (at / "records.json").write_text(json.dumps({"records": [
        {"arm": "baseline", "usage": RUNNER_MISSING_USAGE},
        {"arm": "nexus", "usage": {"input_tokens": 400}}]}))
    got = RC.row_reserve(d, 30)
    check("reserve is the measurable part", got == 400, str(got))


def test_partial_usage_still_measures():
    """A1's records carry only the fields the envelope had. Absent is zero WHEN something else
    in the block was measured; the whole block being null is what is unknown."""
    d = _tmp()
    (_arm(d) / "record.json").write_text(json.dumps(
        {"record": {"usage": dict(RUNNER_MISSING_USAGE, input_tokens=10, output_tokens=5)}}))
    c = RC.consumed(d)
    check("partial usage measured", c["tokens_known"] == 15 and c["unresolved"] == 0,
          json.dumps(c))


def test_corrupt_usage_is_unknown_not_a_partial_sum():
    d = _tmp()
    (_arm(d) / "record.json").write_text(json.dumps(
        {"record": {"usage": {"input_tokens": 10, "output_tokens": "many"}}}))
    c = RC.consumed(d)
    check("corrupt usage -> unresolved", c["unresolved"] == 1 and c["tokens_known"] == 0,
          json.dumps(c))


# --------------------------------------------------------------------------- restart
def test_partial_attempt_is_preserved_not_overwritten():
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    (at / "arms" / "baseline").mkdir(parents=True)
    (at / "arms" / "baseline" / "trace.jsonl").write_text("{}\n")
    moved = RC.preserve_partial(at)
    check("partial moved aside", moved is not None and moved.exists(), str(moved))
    check("original path freed", not at.exists(), str(at))
    check("evidence survives", moved and (moved / "arms" / "baseline" / "trace.jsonl").exists())


def test_completed_attempt_is_not_moved():
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    (at / "arms" / "baseline").mkdir(parents=True)
    (at / "arms" / "baseline" / "trace.jsonl").write_text("{}\n")
    (at / "records.json").write_text("{}")
    check("completed attempt untouched", RC.preserve_partial(at) is None)


# --------------------------------------------------------------------------- identity
class _Plan:
    schedule_digest = "abc123"

TASKS = ("k1", "k2", "k3", "k4")


def _write_prompts(bench: Path, body: str = "BODY", name: str = "test-prompts.json") -> Path:
    """A prompt registration of the shape run_arms_isolated assembles from."""
    bench.mkdir(parents=True, exist_ok=True)
    f = bench / name
    f.write_text(json.dumps({
        "consult": "CONSULT", "environment": "ENV", "tails": {"default": "TAIL"},
        "tasks": {t: {"set": "development", "tail": "default", "body": f"{body}-{t}"}
                  for t in TASKS}}))
    return f


def _cfg(tmp: Path | None = None, **over):
    """A real a1_config.Config, loaded the way the driver loads one.

    Not a raw dict: the digest under test is over the RESOLVED configuration, and a dict that
    skips `a1_config.load` would be testing neither side of the contract.
    """
    tmp = tmp or _tmp()
    bench = tmp / "_bench"
    _write_prompts(bench)
    data = {"source_clone": str(tmp), "pytest_python": str(tmp), "venv_python": str(tmp),
            "nexus_server": str(tmp), "config_version": RC.CONFIG_VERSION, "max_turns": 30,
            "corpus_digest": "0123456789abcdef", "bench": str(bench),
            "prompts": "test-prompts.json"}
    data.update(over)
    f = tmp / "cfg.json"
    f.write_text(json.dumps(data))
    return a1_config.load(f)


def _good_record(tmp: Path, cfg=None) -> Path:
    """A record carrying the identity THE PRODUCER writes.

    Built from `identity.expected` -- the function `run_identity` in run_arms_isolated
    delegates to -- not from the consumer's own expectation. The old version of this fixture
    called `RC.expected_identity` and then handed it straight back to `RC.incompatible`, so it
    checked the consumer against itself and passed while the real producer-consumer pair
    disagreed on every row.
    """
    cfg = cfg or _cfg(tmp)
    want = ident.expected(cfg, "k1", _Plan.schedule_digest, 30)
    rec = tmp / "records.json"
    rec.write_text(json.dumps({"identity": want}))
    return rec


def test_matching_identity_resumes():
    t = _tmp()
    cfg = _cfg(t)
    check("matching identity resumes",
          RC.incompatible(_good_record(t, cfg), cfg, "k1", _Plan(), 30) is None,
          str(RC.incompatible(_good_record(t, cfg), cfg, "k1", _Plan(), 30)))


def test_unchanged_configuration_resumes_across_the_producer_consumer_boundary():
    """The defect this file exists to catch: a valid row, an unchanged configuration, and a
    resume that refused it. The producer hashed `CFG.as_recorded()` (resolved, defaults filled
    in); the consumer hashed the raw configuration file. Different inputs, different digest,
    every real row refused."""
    t = _tmp()
    cfg = _cfg(t)
    produced = ident.expected(cfg, "k1", _Plan.schedule_digest, 30)
    rec = t / "records.json"
    rec.write_text(json.dumps({"identity": produced}))
    why = RC.incompatible(rec, cfg, "k1", _Plan(), 30)
    check("producer identity resumes under an unchanged config", why is None, str(why))
    raw = json.loads((t / "cfg.json").read_text())
    check("digest is over the resolved config, not the raw file",
          ident.config_digest(cfg) != __import__("hashlib").sha256(
              json.dumps(raw, sort_keys=True).encode()).hexdigest()[:16])


def test_runner_does_not_compute_identity_itself():
    """A tripwire, because the two implementations are the defect. run_arms_isolated cannot be
    imported here -- it resolves and preflights a real host configuration at import -- so the
    delegation is asserted against its source."""
    src = (Path(__file__).parent / "run_arms_isolated.py").read_text()
    body = src[src.index("def run_identity()"):src.index("def main()")]
    check("run_identity delegates to identity.expected", "ident.expected(" in body, body[:200])
    check("run_identity hashes nothing itself", "sha256" not in body, body[:200])


def test_changed_prompt_at_the_same_filename_refuses():
    """`prompt_digest` was required to be PRESENT and never compared, so a row produced under
    a different prompt resumed silently. The config mismatch above is what hid it: it refused
    every real row first, so nothing ever reached this comparison."""
    t = _tmp()
    cfg = _cfg(t)
    rec = _good_record(t, cfg)
    _write_prompts(t / "_bench", body="A DIFFERENT PROMPT")     # same filename, new contents
    why = RC.incompatible(rec, cfg, "k1", _Plan(), 30)
    check("changed prompt refuses", why is not None and "prompt_digest" in (why or ""), str(why))


def test_prompt_digest_is_per_task():
    t = _tmp()
    cfg = _cfg(t)
    rec = _good_record(t, cfg)                                   # written for k1
    why = RC.incompatible(rec, cfg, "k2", _Plan(), 30)
    check("another task's prompt refuses", why is not None and "prompt_digest" in (why or ""),
          str(why))


def test_every_identity_field_is_compared():
    """It used to compare four fields and accept everything else -- including a different
    PRODUCT revision, which is the comparison the function exists to make."""
    for field in RC.IDENTITY_FIELDS:
        t = _tmp()
        cfg = _cfg(t)
        rec = _good_record(t, cfg)
        data = json.loads(rec.read_text())
        data["identity"][field] = "CHANGED-VALUE"
        rec.write_text(json.dumps(data))
        why = RC.incompatible(rec, cfg, "k1", _Plan(), 30)
        check(f"{field} mismatch refused", why is not None and field in why, str(why))


def test_absent_identity_field_refuses():
    for field in RC.IDENTITY_FIELDS:
        t = _tmp()
        cfg = _cfg(t)
        rec = _good_record(t, cfg)
        data = json.loads(rec.read_text())
        data["identity"].pop(field, None)
        rec.write_text(json.dumps(data))
        why = RC.incompatible(rec, cfg, "k1", _Plan(), 30)
        check(f"{field} absent refused", why is not None and field in why, str(why))


def test_prompt_digest_is_one_of_the_pooling_fields():
    check("prompt_digest is compared, not merely required",
          "prompt_digest" in RC.IDENTITY_FIELDS, str(RC.IDENTITY_FIELDS))


def test_records_without_identity_are_refused():
    t = _tmp()
    cfg = _cfg(t)
    rec = t / "records.json"
    rec.write_text(json.dumps({"max_turns": 30}))
    why = RC.incompatible(rec, cfg, "k1", _Plan(), 30)
    check("no identity block refused", why and "identity" in why, str(why))


# --------------------------------------------------------------------------- launch evidence
def test_launched_arm_without_accounting_is_unresolved():
    """The runner buffers the whole trace until the subprocess returns. An interruption inside
    that window leaves a launch marker and nothing else -- the window that lost k4."""
    d = _tmp()
    arm = d / "c30" / "run-k1" / "attempt1" / "arms" / "baseline"
    arm.mkdir(parents=True)
    (arm / "launched.json").write_text(json.dumps({"arm": "baseline"}))
    c = RC.consumed(d)
    check("launched-but-unaccounted is unresolved", c["unresolved"] == 1, json.dumps(c))
    check("launched-but-unaccounted counts as a run", c["arm_runs"] == 1, json.dumps(c))


def test_launch_evidence_is_preserved_on_restart():
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    (at / "arms" / "baseline").mkdir(parents=True)
    (at / "arms" / "baseline" / "launched.json").write_text("{}")
    moved = RC.preserve_partial(at)
    check("launch evidence moved aside", moved is not None and moved.exists(), str(moved))
    check("marker survives", moved and (moved / "arms" / "baseline" / "launched.json").exists())


def test_prepared_row_is_distinguished_from_a_launched_arm():
    """A row prepared but never started consumed nothing; it must not be reported unresolved."""
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    at.mkdir(parents=True)
    (at / "launch.json").write_text(json.dumps({"run_id": "x"}))
    c = RC.consumed(d)
    check("prepared row is not consumption", c["unresolved"] == 0 and c["arm_runs"] == 0,
          json.dumps(c))
    check("prepared row is still preserved", RC.preserve_partial(at) is not None)


# --------------------------------------------------------------------------- gate predicates
def test_gate_rejects_a_failing_test_run():
    """`1 failed, 23 passed` passed the previous predicate."""
    outs = {
        "1 failed, 23 passed in 0.1s": False,
        "23 passed in 0.02s": True,
        "no tests ran in 0.01s": False,
        "ERROR tests/x.py - Interrupted: 1 error during collection": False,
    }
    for text, want in outs.items():
        m = V.PYTEST_SUMMARY.search(text)
        n = int(m.group(1)) if m else 0
        bad = bool(V.PYTEST_BAD.search(text))
        got = n > 0 and not bad
        check(f"pytest predicate {text[:28]!r}", got == want, f"got {got} want {want}")


def test_gate_requires_env_argument():
    """The gate once built a restricted environment and then never passed it."""
    import inspect
    for fn in (V.check_interpreter, V.check_tests, V.check_heredoc, V.check_scratch):
        params = list(inspect.signature(fn).parameters)
        check(f"{fn.__name__} takes env", "env" in params, str(params))
    src = inspect.getsource(V._sh)
    check("_sh passes env to subprocess", "env=env" in src, src)


def test_not_tested_is_not_passed():
    """`cross_arm_read_blocked: None` means untested. Untested must not report as passing."""
    # other_profile None -> heredoc_probe reports cross_arm_read_blocked as None (untested).
    # The gate must map that to a FAILURE, not a pass.
    import isolation
    real = isolation.heredoc_probe
    isolation.heredoc_probe = lambda *a, **k: {"tmpprefix_set": True,
                                               "cross_arm_read_blocked": None}
    try:
        r = V.check_heredoc_private(Path("."), Path("."), {"TMPPREFIX": "/x/y"}, None, None)
        check("None is not a pass", r["passed"] is False, json.dumps(r))
    finally:
        isolation.heredoc_probe = real


# --------------------------------------------------------------------------- driver paths
def _stub_configs(tmp: Path, ceilings=(30, 45, 60)) -> dict:
    """Temporary, self-contained configs. The real calib-config-*.json carry per-host absolute
    paths and do not exist in CI, so these tests must not read them."""
    out = {}
    d = tmp / "_configs"
    d.mkdir(parents=True, exist_ok=True)
    bench = tmp / "_bench"
    _write_prompts(bench)
    for c in ceilings:
        f = d / f"calib-config-{c}.json"
        # Loadable by a1_config: the driver resolves the configuration now rather than reading
        # the file as a dict, because that is what the runner records its digest from. The
        # paths are never opened (no preflight here) but the fields must exist.
        f.write_text(json.dumps({
            "source_clone": str(tmp), "pytest_python": str(tmp), "venv_python": str(tmp),
            "nexus_server": str(tmp), "config_version": RC.CONFIG_VERSION, "max_turns": c,
            "corpus_digest": "0123456789abcdef", "bench": str(bench),
            "prompts": "test-prompts.json"}))
        out[c] = f
    return out


def _stub_driver(tmp: Path, cap: int, tokens_per_arm: int, seed_reserve=None,
                 runner_rc: int = 0, launches: list | None = None,
                 no_usage_at: tuple | None = None, ceilings: tuple = (30, 45),
                 unscored_at: tuple | None = None):
    """Run the real driver with a stubbed runner. No model, no sandbox, no network.

    The published revision crashed HERE, on the line after the last row, with
    `NameError: name 'spent' is not defined` -- the token-accounting change had removed the
    function the summary block still called. --dry-run returns before that line, so every
    rehearsal passed while the only path that writes a summary was broken.

    `unscored_at` is an exact `(ceiling, task)` pair whose every arm gets an EXCLUDED
    terminal -- what the runner writes when an arm never reached the model.

    `no_usage_at` is an exact `(ceiling, task, arm)` triple, because WHERE the unaccounted
    arm-run sits is the whole question. An earlier version of this stub injected by task NAME
    while running ceilings 30 and 45, so injecting at "k4" -- meant to be the final row -- hit
    c30/k4, whose blockage the next ceiling's first pre-launch check caught. It reported a pass
    for a terminal path it never reached.
    """
    import run_calibration as R
    real_run, real_fix, real_cap = R.subprocess.run, R.build_fixture_for, R.CAP_TOKENS
    real_seed, real_cfg = R.INITIAL_ROW_RESERVE, R.config_for
    cfgs = _stub_configs(tmp, ceilings=ceilings)
    R.config_for = lambda c: cfgs[c]
    # Identities are built BEFORE the stub is installed. `R.subprocess` is the shared module
    # object, so stubbing `R.subprocess.run` also stubs the `git` calls `identity.rev()` makes
    # -- which silently emptied the product and harness revisions of every row this stub wrote,
    # and would have made any resume test here meaningless.
    digest = R.sched.load(R.SCHEDULE).schedule_digest
    idents = {(c, t): ident.expected(a1_config.load(cfgs[c]), t, digest, c)
              for c in ceilings for t in TASKS}

    def fake_run(cmd, **kw):
        root, task, attempt = Path(cmd[2]), cmd[3], cmd[4]
        ceiling = int(root.name[1:])
        if launches is not None:
            launches.append(f"{root.name}/{task}")
        at = root / f"run-{task}" / f"attempt{attempt}"
        if runner_rc != 0:
            (at / "arms" / "baseline").mkdir(parents=True, exist_ok=True)
            (at / "arms" / "baseline" / "launched.json").write_text('{"arm":"baseline"}')

            class F:
                returncode = runner_rc
            return F()
        recs = []
        # The runner's own missing-usage shape, not an absent key: four nulls in a non-empty
        # dict, which is what it writes when the envelope carried no usage block.
        blank = {k: None for k in ("input_tokens", "output_tokens",
                                   "cache_read_input_tokens", "cache_creation_input_tokens")}
        for arm in ("baseline", "nexus", "notes"):
            (at / "arms" / arm).mkdir(parents=True, exist_ok=True)
            (at / "arms" / arm / "trace.jsonl").write_text('{"type":"result","usage":{}}\n')
            # A terminal verdict, as the runner writes one. `scored: False` is an excluded
            # terminal -- a harness, auth or transport fault, which is not a result.
            excluded = unscored_at == (ceiling, task)
            recs.append({"arm": arm,
                         "usage": dict(blank) if no_usage_at == (ceiling, task, arm)
                         else {"input_tokens": 0 if excluded else tokens_per_arm},
                         "terminal": {"terminal": "env_fail" if excluded else "ok",
                                      "scored": not excluded}})
        (at / "records.json").write_text(json.dumps({
            "task": task, "attempt": int(attempt), "schedule_digest": digest,
            "identity": idents[(ceiling, task)], "records": recs}))

        class D:
            returncode = 0
        return D()

    R.subprocess.run = fake_run
    R.build_fixture_for = lambda *a, **k: (a[1] / f"run-{a[0]}" / "base" / a[0]).mkdir(
        parents=True, exist_ok=True)
    R.CAP_TOKENS = cap
    if seed_reserve is not None:
        R.INITIAL_ROW_RESERVE = seed_reserve
    try:
        rc = R.main(["run_calibration.py", str(tmp), "--ceilings",
                     ",".join(str(c) for c in ceilings)])
    finally:
        R.subprocess.run, R.build_fixture_for, R.CAP_TOKENS = real_run, real_fix, real_cap
        R.INITIAL_ROW_RESERVE, R.config_for = real_seed, real_cfg
    summary = tmp / "calibration-summary.json"
    return rc, (json.loads(summary.read_text()) if summary.exists() else None)


def test_driver_completes_and_writes_a_summary():
    tmp = _tmp()
    rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000)
    check("completion path returns 0", rc == 0, str(rc))
    check("completion writes a summary", summary is not None)
    if summary:
        check("summary reports completion",
              summary["stopping_reason"] == "completed", json.dumps(summary)[:200])
        check("summary counts every arm-run", summary["arm_runs"] == 24, str(summary["arm_runs"]))
        check("summary has no unresolved", summary["unresolved_arm_runs"] == [],
              str(summary["unresolved_arm_runs"]))
        check("overshoot is certain", summary["overshoot_certain"] is True)


def test_driver_stops_on_budget_and_writes_a_summary():
    tmp = _tmp()
    # Two rows fit; the third needs more than the cap allows, so it must stop BEFORE starting
    # it. The seeded reserve matches the stub's row size so the first row is not refused.
    rc, summary = _stub_driver(tmp, cap=7000, tokens_per_arm=1000, seed_reserve=3000)
    check("budget path returns 0", rc == 0, str(rc))
    check("budget stop writes a summary", summary is not None)
    if summary:
        check("summary reports the budget stop",
              summary["stopping_reason"] == "budget", json.dumps(summary)[:200])
        check("summary names where it stopped", summary["stopped_at"] is not None,
              str(summary.get("stopped_at")))
        check("grid is partial", summary["arm_runs"] < 24, str(summary["arm_runs"]))


def test_failed_row_stops_the_whole_sweep():
    """A failing runner used to break only the inner loop, so one broken runner launched every
    ceiling in turn and the driver still exited 0."""
    tmp = _tmp()
    launches: list = []
    rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000,
                               runner_rc=1, launches=launches)
    check("exactly one launch", len(launches) == 1, str(launches))
    check("driver exits nonzero", rc != 0, str(rc))
    check("summary still written", summary is not None)
    if summary:
        check("summary says gate_or_error",
              summary["stopping_reason"] == "gate_or_error", summary["stopping_reason"])
        check("failed launch is unresolved", summary["unresolved_arm_runs"] != [],
              str(summary["unresolved_arm_runs"]))


def test_driver_survives_the_runners_missing_usage_record():
    """End to end, through the real driver: a row whose usage block is the runner's four nulls.

    `consumed` raised TypeError on it and the driver died with no calibration-summary.json --
    on the one path where the accounting is the thing that matters. Injected at an EXACT
    (ceiling, task, arm), because where the unaccounted arm-run sits decides which code path
    notices it.
    """
    for label, at in (("first row", (30, "k1", "baseline")),
                      ("last row of the first ceiling", (30, "k4", "baseline"))):
        tmp = _tmp()
        launches: list = []
        rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000,
                                   launches=launches, no_usage_at=at, ceilings=(30, 45))
        check(f"{label}: driver exits nonzero", rc != 0, str(rc))
        check(f"{label}: summary still written", summary is not None)
        if summary:
            check(f"{label}: the arm-run is unresolved",
                  len(summary["unresolved_arm_runs"]) == 1,
                  str(summary["unresolved_arm_runs"]))
            check(f"{label}: consumption is not certain",
                  summary["consumption_certain"] is False, json.dumps(summary)[:200])
            check(f"{label}: overshoot is not a number",
                  summary["overshoot_tokens"] is None, json.dumps(summary)[:200])
            check(f"{label}: reason names the accounting",
                  summary["stopping_reason"] == "unresolved_accounting",
                  summary["stopping_reason"])


def test_the_sweeps_very_last_arm_run_cannot_report_completion():
    """The final arm of the final row of the final ceiling -- and then a resume over it.

    `stopping_reason` and the exit code used to be decided by `blocked`, which only the NEXT
    row's pre-launch check can set. After the last row there is no next row, and on a resume
    where every row is skipped there is no check at all, so an unaccounted sweep reported
    `completed` and exited 0 in exactly the two cases where nothing would look again.

    The predecessor of this test injected by task name over ceilings 30 and 45, so its
    "final row" was c30/k4 and the next ceiling's first check caught it. It passed without
    ever reaching the path it was named for.
    """
    tmp = _tmp()
    launches: list = []
    last = (60, "k4", "notes")
    rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000, launches=launches,
                               no_usage_at=last, ceilings=(30, 45, 60))
    check("every row ran", len(launches) == 12, str(len(launches)))
    check("final arm: driver exits nonzero", rc != 0, str(rc))
    check("final arm: summary written", summary is not None)
    if summary:
        check("final arm: not reported as completed",
              summary["stopping_reason"] == "unresolved_accounting",
              summary["stopping_reason"])
        check("final arm: the arm-run is unresolved",
              len(summary["unresolved_arm_runs"]) == 1, str(summary["unresolved_arm_runs"]))
        check("final arm: consumption not certain", summary["consumption_certain"] is False)

    # Resume the same directory. Every row has a records.json whose identity matches, so every
    # row is skipped and no pre-launch check runs at all.
    resumed: list = []
    rc2, summary2 = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000, launches=resumed,
                                 ceilings=(30, 45, 60))
    check("resume launched nothing", resumed == [], str(resumed))
    check("resume: driver exits nonzero", rc2 != 0, str(rc2))
    check("resume: not reported as completed",
          summary2 and summary2["stopping_reason"] == "unresolved_accounting",
          str(summary2 and summary2["stopping_reason"]))


def test_a_row_that_measured_nothing_stops_the_sweep():
    """The 2026-09-14 void sweep. The forwarder was not running, every arm returned
    `API Error: Connection refused`, and all 36 arm-runs recorded an excluded terminal with an
    honest zero usage block. Nothing was unresolved, no runner exited nonzero, and the driver
    wrote `"stopping_reason": "completed"` and exited 0 having measured nothing at all -- after
    nine and a half minutes per row for two hours.

    `env_fail` is a per-arm classification, not a runner failure, so only the verdict says so.
    """
    tmp = _tmp()
    launches: list = []
    rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000, launches=launches,
                               unscored_at=(30, "k1"), ceilings=(30, 45, 60))
    check("stops at the first barren row", len(launches) == 1, str(launches))
    check("driver exits nonzero", rc != 0, str(rc))
    check("summary written", summary is not None)
    if summary:
        check("reason names the instrument",
              summary["stopping_reason"] == "instrument_fault", summary["stopping_reason"])
        check("nothing scored", summary["scored_arm_runs"] == 0, str(summary["scored_arm_runs"]))
        check("three excluded", summary["excluded_arm_runs"] == 3,
              str(summary["excluded_arm_runs"]))
        check("names the barren row",
              len(summary["rows_with_no_scored_arm_run"]) == 1,
              str(summary["rows_with_no_scored_arm_run"]))


def test_a_barren_row_later_in_the_grid_also_stops_it():
    """Including at the very last row, where no pre-launch check follows."""
    tmp = _tmp()
    launches: list = []
    rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000, launches=launches,
                               unscored_at=(60, "k4"), ceilings=(30, 45, 60))
    check("ran the whole grid", len(launches) == 12, str(len(launches)))
    check("final barren row: exits nonzero", rc != 0, str(rc))
    check("final barren row: instrument_fault",
          summary and summary["stopping_reason"] == "instrument_fault",
          str(summary and summary["stopping_reason"]))
    check("the scored rows are still counted",
          summary and summary["scored_arm_runs"] == 33, str(summary and summary["scored_arm_runs"]))


def test_resume_refuses_a_row_that_measured_nothing():
    """Resuming over a barren row would treat an instrument failure as a completed row, and
    its identity matches, so nothing else would refuse it. It refuses THROUGH the summary --
    a durable record and a nonzero exit, not a raise past the line that writes one."""
    tmp = _tmp()
    _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000,
                 unscored_at=(30, "k1"), ceilings=(30, 45, 60))
    resumed: list = []
    rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000,
                               launches=resumed, ceilings=(30, 45, 60))
    check("resume launches nothing", resumed == [], str(resumed))
    check("resume exits nonzero", rc != 0, str(rc))
    check("resume writes a summary", summary is not None)
    check("resume names the instrument",
          summary and summary["stopping_reason"] == "instrument_fault",
          str(summary and summary["stopping_reason"]))


def test_a_quarantined_barren_row_does_not_poison_later_sweeps():
    """The void rows stay in the scratch on purpose -- the token accounting must keep counting
    them -- so a historical barren row must not make every later summary say the instrument is
    down. The stopping reason is about THIS sweep; the grid-wide counts stay in the summary as
    information."""
    tmp = _tmp()
    _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000,
                 unscored_at=(30, "k1"), ceilings=(30,))
    # quarantine it the way an operator would: rename the ceiling directory
    (tmp / "c30").rename(tmp / "v2-void-forwarder-down-c30")
    rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000, ceilings=(30,))
    check("a fresh grid completes", rc == 0, str(rc))
    check("and says so", summary["stopping_reason"] == "completed",
          summary["stopping_reason"])
    check("the quarantined row is still reported",
          summary["excluded_arm_runs"] == 3, str(summary["excluded_arm_runs"]))
    check("and the new rows are scored", summary["scored_arm_runs"] == 12,
          str(summary["scored_arm_runs"]))


def test_one_scored_arm_is_enough_to_continue():
    """Protocol-a1 §10 excludes env_fail from scored SETS; it does not make a row worthless.
    The rule here is about a row that measured NOTHING, not about any exclusion at all."""
    tmp = _tmp()
    rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000, ceilings=(30,))
    check("ordinary sweep still completes", rc == 0, str(rc))
    check("all arms scored", summary["scored_arm_runs"] == 12, str(summary["scored_arm_runs"]))
    check("none excluded", summary["excluded_arm_runs"] == 0, str(summary["excluded_arm_runs"]))


def test_a_fully_accounted_sweep_still_completes():
    """The control for the two above: nothing outstanding, so `completed` and exit 0 stand."""
    tmp = _tmp()
    rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000, ceilings=(30, 45, 60))
    check("control: exits 0", rc == 0, str(rc))
    check("control: completed", summary["stopping_reason"] == "completed",
          summary["stopping_reason"])
    check("control: 36 arm-runs", summary["arm_runs"] == 36, str(summary["arm_runs"]))
    check("control: consumption certain", summary["consumption_certain"] is True)


def test_cross_arm_probe_needs_a_working_sibling():
    """`sentinel not in stdout` is also true when the sibling sandbox never ran at all."""
    import isolation
    real = isolation.subprocess.run

    def fake(sibling_rc):
        n = {"i": 0}

        def _run(cmd, **kw):
            n["i"] += 1

            class First:
                returncode, stdout, stderr = 0, "heredoc-ok\n", ""

            class Sibling:
                returncode = sibling_rc
                stdout = "CONTROL-FOR-ARM-B\n" if sibling_rc == 0 else ""
                stderr = "" if sibling_rc == 0 else "sandbox-exec: profile missing"
            return First() if n["i"] == 1 else Sibling()
        return _run

    for label, rc_, want_blocked in (("broken sibling", 1, None), ("working sibling", 0, True)):
        t = _tmp()
        (t / "tmp").mkdir()
        (t / "sib").mkdir()
        (t / "sib" / "p.sb").write_text("(version 1)")
        isolation.subprocess.run = fake(rc_)
        try:
            r = isolation.heredoc_probe(Path("pA"), t, {"TMPPREFIX": str(t / "tmp" / "zsh")},
                                        t / "sib" / "p.sb", t / "sib")
        finally:
            isolation.subprocess.run = real
        check(f"{label}: cross_arm_read_blocked", r["cross_arm_read_blocked"] is want_blocked,
              json.dumps({k: v for k, v in r.items() if "tail" not in k}))


def test_missing_sibling_profile_is_not_a_pass():
    """Deterministic: the first heredoc call is stubbed so this never shells out to
    sandbox-exec, which does not exist off macOS."""
    import isolation
    real = isolation.subprocess.run

    def _run(cmd, **kw):
        class OK:
            returncode, stdout, stderr = 0, "heredoc-ok\n", ""
        return OK()
    isolation.subprocess.run = _run
    try:
        t = _tmp()
        (t / "tmp").mkdir()
        r = isolation.heredoc_probe(Path("/nope/pA"), t, {"TMPPREFIX": str(t / "tmp" / "zsh")},
                                    Path("/nope/pB"), Path("/nope/cwd"))
    finally:
        isolation.subprocess.run = real
    check("missing sibling -> not tested", r["cross_arm_read_blocked"] is None,
          json.dumps({k: v for k, v in r.items() if "tail" not in k}))


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
        except Exception as e:                                   # a crash is a failure
            FAILS.append(f"{t.__name__}: raised {type(e).__name__}: {e}")
    for f in FAILS:
        print(f"  FAIL {f}")
    print(f"{len(tests) - len({f.split(':')[0] for f in FAILS})}/{len(tests)} "
          f"counterexample groups pass")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
