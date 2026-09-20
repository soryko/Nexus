"""Controls on the PRODUCTION path and on the rehearsal's egress.

The egress checks here exist because of an incident: on 2026-09-20 this rehearsal ran two
arm-runs against the real model endpoint and spent 2 669 285 tokens, while paid execution
was stopped. See INCIDENT-a3-unauthorized-spend.md. Every check below is one of the
conditions that would have prevented it, asserted so it cannot silently come back.

    <venv>/bin/python -m pytest benchmarks/agent/test_a3_production.py -q

These are static and fixture-level: they do not start a stub, a forwarder or an arm. The
live path is exercised by `rehearse_a3_production.py` on a macOS host with `sandbox-exec`
and the A2-R fixture trees, which a Linux runner does not have.
"""
from __future__ import annotations

import inspect
import json
import socket
import sys
import types
from pathlib import Path

import pytest

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a3_ledger as L                                                      # noqa: E402
import a3_pipeline as P                                                    # noqa: E402
import a3_prompts as AP                                                    # noqa: E402
import isolation                                                           # noqa: E402
import rehearse_a3_production as RP                                        # noqa: E402
import stub_turns as ST                                                    # noqa: E402


# ---------------------------------------------------------------------------------------
# EGRESS. The incident, as checks.
# ---------------------------------------------------------------------------------------

def test_the_rehearsal_does_not_use_the_production_forwarder_port():
    """8899 is the configured port, so it is the one port guaranteed to be occupied."""
    frozen = json.loads((BENCH / "a3-config-45.json").read_text())
    assert RP.FORWARDER_PORT != frozen["forwarder_port"]
    assert RP.FORWARDER_PORT != 8899


def test_the_rehearsal_never_inherits_a_real_api_key():
    """`setdefault` left a real key in the child environment. It must be OVERWRITTEN."""
    src = inspect.getsource(RP.main)
    assert 'runner_env["DEEPSEEK_API_KEY"] = STUB_KEY' in src
    assert "setdefault(\"DEEPSEEK_API_KEY\"" not in src
    assert "stub" in RP.STUB_KEY.lower()


def test_a_port_already_in_use_is_refused():
    """A listener the rehearsal did not start is a refusal, not a success."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        assert RP._port_free(s.getsockname()[1]) is False
    finally:
        s.close()


def test_a_dead_child_is_not_a_slow_start():
    """`_listening` must fail when the process it is waiting for has exited."""
    class Dead:
        def poll(self):
            return 1
    assert RP._listening(65535, timeout=2.0, proc=Dead()) is False


def test_the_upstream_control_refuses_when_the_token_does_not_come_back(monkeypatch):
    """The control that actually matters: a positive proof of WHERE egress goes, taken
    before any arm-run. A forwarder pointed at a paid endpoint cannot echo the token."""
    import urllib.request

    class Reply:
        def read(self):
            return b"event: content_block_delta\\ndata: {\"text\": \"something else\"}\\n"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Reply())
    with pytest.raises(RP.NotTheStub, match="instance token is absent"):
        RP.assert_stub_upstream(9999, "STUB-INSTANCE-deadbeef")


def test_the_upstream_control_passes_only_on_its_own_token(monkeypatch):
    import urllib.request
    token = "STUB-INSTANCE-0123456789abcdef"

    class Reply:
        def read(self):
            return f'data: {{"text": "{token}"}}'.encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Reply())
    RP.assert_stub_upstream(9999, token)                      # must not raise


def test_a_failed_upstream_control_refuses_rather_than_continuing(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(RP.NotTheStub, match="REFUSING to launch"):
        RP.assert_stub_upstream(9999, "tok")


def test_the_stub_echoes_its_instance_token_to_a_non_loop_request():
    """A request with no client-side tool definitions is not a loop request; it is the
    control's shape, and the stub answers it with the token rather than advancing a script."""
    assert hasattr(ST.Handler, "instance_token")
    src = inspect.getsource(ST.Handler)
    assert "Handler.instance_token or" in src


def test_the_rehearsal_config_is_not_the_launch_config(tmp_path):
    """`forwarder_port` comes from the CONFIG, so a rehearsal on a private port needs its
    own -- and it must be impossible to mistake for the frozen one."""
    cfg_path = RP.rehearsal_config(tmp_path)
    cfg = json.loads(cfg_path.read_text())
    assert cfg["forwarder_port"] == RP.FORWARDER_PORT
    assert cfg["config_version"] == "a3-v1-REHEARSAL"
    assert cfg_path.parent == tmp_path              # in the scratch, not beside the frozen one
    frozen = json.loads((BENCH / "a3-config-45.json").read_text())
    assert frozen["config_version"] == "a3-v1" and frozen["forwarder_port"] == 8899


def test_a_rehearsal_row_is_named_as_not_an_A3_result():
    src = inspect.getsource(__import__("run_a3"))
    assert "a3-v1-REHEARSAL" in src
    assert "No row produced here is an" in src


# ---------------------------------------------------------------------------------------
# THE PRODUCTION ADAPTER: the five things it changes.
# ---------------------------------------------------------------------------------------

def test_the_adapter_assembles_the_policy_aware_prompt():
    """`run_arms_isolated` assembles one prompt through `task_set.assemble` and has no
    notion of a policy. Computing digests in a preflight establishes nothing about what
    reaches the model unless the runner sends that prompt."""
    src = inspect.getsource(__import__("run_a3"))
    assert "AP.assemble(task, policy)" in src
    assert "R.PROMPT = AP.assemble" in src


def test_the_adapter_requires_the_A3_boundary_controls():
    src = inspect.getsource(__import__("run_a3"))
    assert "require=isolation.REQUIRE_A3" in src
    assert "mark_refusal" in src                 # a refusal is recorded, not treated as zero


def test_the_adapter_denies_the_sibling_policy():
    """`boundary_paths` denies sibling ARMS within one OUT; A3's sibling is the other
    POLICY, a peer directory, which has to be named."""
    src = inspect.getsource(__import__("run_a3"))
    assert "_other" in src and "R.boundary_paths = paths" in src


def test_the_adapter_checks_a_fatal_stop_before_every_arm_run():
    """An admitted pair does not authorise its second member."""
    src = inspect.getsource(__import__("run_a3").run_pair)
    body = src[src.index("R.ARMS = {ARM"):]            # the execution half
    assert "L.fatal_stop(scratch, registered)" in body
    assert body.index("L.fatal_stop(") < body.index("R.invoke(")


def test_the_adapter_marks_the_launch_before_invoking():
    src = inspect.getsource(__import__("run_a3").run_pair)
    assert src.index("L.mark_launch(") < src.index("R.invoke(")


def test_the_two_markers_are_different_files():
    """The runner writes its own marker at the same path. One name for both meant the
    runner's subset silently replaced A3's policy and prompt digest."""
    assert L.A3_MARKER != L.LAUNCH_MARKER
    assert set(L.MARKERS) == {L.A3_MARKER, L.LAUNCH_MARKER}


def test_either_marker_proves_a_launch(tmp_path):
    d = P.arm_run_dir(tmp_path, "k1", 1, "A")
    d.mkdir(parents=True)
    (d / L.LAUNCH_MARKER).write_text("{}")        # only the runner's
    assert L.read(tmp_path).unresolved == ["k1/A/1"]


def test_the_registered_prompt_digests_are_what_the_adapter_would_send():
    """The digests the verification compares against are computed from the registration,
    not copied from the preflight."""
    assert AP.digest("k1", "A") == "5f4f1e210aa7f7ea"
    assert AP.digest("k1", "B") == "2b00dc58add11488"
    assert AP.digest("k1", "A") != AP.digest("k1", "B")


def test_outbound_verification_requires_both_policies(tmp_path):
    """A log carrying only A's prompt must not pass: that would be one policy sent twice."""
    log = tmp_path / "stub.json"
    log.write_text(json.dumps({"requests_received": 2, "scripted_turns_served": 2,
                               "requests": [{"user_text_digests": [AP.digest("k1", "A")]}]}))
    out = RP.verify_outbound_prompts(log, "k1")
    assert out["per_policy"]["A"]["observed_outbound"] is True
    assert out["per_policy"]["B"]["observed_outbound"] is False
    assert out["all_registered_prompts_observed"] is False
    assert out["policies_sent_different_prompts"] is False


def test_outbound_verification_passes_when_both_are_observed(tmp_path):
    log = tmp_path / "stub.json"
    log.write_text(json.dumps({"requests_received": 4, "scripted_turns_served": 4,
                               "requests": [{"user_text_digests": [AP.digest("k1", "A")]},
                                            {"user_text_digests": [AP.digest("k1", "B")]}]}))
    out = RP.verify_outbound_prompts(log, "k1")
    assert out["all_registered_prompts_observed"] is True
    assert out["policies_sent_different_prompts"] is True


# ---------------------------------------------------------------------------------------
# GAP 1 -- the budget gate was never reached from production.
# ---------------------------------------------------------------------------------------

def test_the_adapter_admits_the_pair_on_BUDGET_before_launching():
    """`may_admit_pair` lives in `a3_ledger` and was called only by the rehearsal's driver.
    Production checked `fatal_stop` alone, so the threshold was never consulted and both
    invocations were reached at any level of consumption."""
    src = inspect.getsource(__import__("run_a3").run_pair)
    assert "L.may_admit_pair(" in src
    assert src.index("L.may_admit_pair(") < src.index("for policy in order:")


def test_admission_is_asked_once_and_the_fatal_stop_per_arm_run():
    """They are different questions. Budget once for the pair; safety before each member."""
    src = inspect.getsource(__import__("run_a3").run_pair)
    body = src[src.index("R.ARMS = {ARM"):]            # the execution half
    assert "L.may_admit_pair(" not in body          # not re-asked per arm-run
    assert "L.fatal_stop(" in body
    assert src.count("L.may_admit_pair(") == 1


def test_a_refused_pair_records_a_refusal_for_both_policies():
    """A pair the budget refuses is recorded as refused for BOTH policies, and nothing is
    launched -- a refusal is not an arm-run that spent nothing."""
    src = inspect.getsource(__import__("run_a3").run_pair)
    head = src[:src.index("R.ARMS = {ARM")]            # everything before execution
    assert "L.mark_refusal(" in head
    assert head.index("L.mark_refusal(") > head.index("L.may_admit_pair(")
    assert "no arm-run was launched" in head


def test_the_adapter_passes_the_registered_plan_not_the_schedule():
    """The first version passed the Schedule to the ledger, which wants the registered Plan.
    Two objects, one name `plan`, and it crashed on the gate's first production run."""
    src = inspect.getsource(__import__("run_a3").run_pair)
    assert "registered = A3_PLAN" in src
    assert "L.may_admit_pair(scratch, task, attempt, registered)" in src
    assert "L.fatal_stop(scratch, registered)" in src


def test_the_budget_refuses_at_the_reported_level(tmp_path):
    """26M consumed plus a 4 831 570 reservation exceeds 30M, so no new pair starts."""
    import json as _j
    per = 26_000_000 // 4
    for t, a, pol in (("k2", 1, "A"), ("k2", 1, "B"), ("k2", 2, "A"), ("k2", 2, "B")):
        L.mark_launch(tmp_path, t, a, pol, identity={}, max_turns=45, wall_clock_s=600,
                      prompt_digest="x")
        out, cache = int(per * 0.01), int(per * 0.25)
        (P.arm_run_dir(tmp_path, t, a, pol) / "record.json").write_text(_j.dumps({"usage": {
            "input_tokens": per - out - cache, "output_tokens": out,
            "cache_read_input_tokens": cache, "cache_creation_input_tokens": 0}}))
    assert L.read(tmp_path).tokens_known == 26_000_000
    g = L.may_admit_pair(tmp_path, "k1", 1)
    assert g["may_start"] is False and g["gate"] == "budget"


# ---------------------------------------------------------------------------------------
# GAP 2 -- the functional result has to reach the reporter.
# ---------------------------------------------------------------------------------------

def test_the_adapter_writes_the_artifact_the_reporter_reads():
    """The production writer recorded the functional result inside `record.json` while
    `normalise` looked for `functional.json`, so every production row arrived as unknown
    with the scorer having settled it. An artifact-presence check cannot see that."""
    src = inspect.getsource(__import__("run_a3").run_pair)
    assert 'functional.json' in src
    assert src.index("R.score(") < src.index('"functional.json"')
    pipeline = (BENCH / "a3_pipeline.py").read_text()
    assert 'd / "functional.json"' in pipeline           # one name, both sides


def test_a_record_without_the_artifact_is_unknown_not_a_pass(tmp_path):
    """The failure mode, pinned: a row carrying the scorer's verdict only inside
    `record.json` must read as unresolved rather than being silently believed."""
    import json as _j
    d = P.arm_run_dir(tmp_path, "k1", 1, "A")
    d.mkdir(parents=True)
    (d / "record.json").write_text(_j.dumps(
        {"record": {"scored": {"passed": True}, "usage": {"input_tokens": 10,
                                                          "output_tokens": 1,
                                                          "cache_read_input_tokens": 0,
                                                          "cache_creation_input_tokens": 0}}}))
    assert not (d / "functional.json").exists()
    # `normalise` reads functional.json and nothing else for this field
    assert 'functional = json.loads(fn.read_text()).get("passed") if fn.is_file() else None' \
        in (BENCH / "a3_pipeline.py").read_text()


# -- the rehearsal's own exit status -------------------------------------------------
# A mocked runner exiting 9, a policy that wrote nothing, and a reporter that raised were
# all RECORDED and then discarded by an unconditional `return 0`.

def _policy_row(usable=True, **kw):
    """One policy's artifact row. `usable` mirrors what the reporter requires: a trace AND
    a patch. Launch markers and a record alone are what "launched, no usable artifacts"
    means, and are NOT a successful rehearsal."""
    row = {"dir": "/x", "launched.json": True, "record.json": True,
           "trace.jsonl": usable, "patch.diff": usable}
    row.update(kw)
    return row


def _ok_report(**kw):
    report = {"runner_exit": 0,
              "artifacts": {"per_policy": {"A": _policy_row(), "B": _policy_row()}},
              "outbound_prompts": {
                  "per_policy": {"A": {"observed_outbound": True},
                                 "B": {"observed_outbound": True}},
                  "all_registered_prompts_observed": True,
                  "policies_sent_different_prompts": True},
              "decision": {"decision": "accept_for_further_development"}}
    report.update(kw)
    return report


def test_a_clean_rehearsal_reports_no_failures():
    assert RP.rehearsal_failures(_ok_report(), dry_run=False) == []


def test_a_runner_exiting_non_zero_fails_the_rehearsal():
    f = RP.rehearsal_failures(_ok_report(runner_exit=9), dry_run=False)
    assert any("exited 9" in x for x in f)


def test_a_policy_that_wrote_no_artifacts_fails_the_rehearsal():
    r = _ok_report()
    r["artifacts"]["per_policy"]["B"] = {"dir": "/x/B"}
    f = RP.rehearsal_failures(r, dry_run=False)
    assert any("policy B left no usable artifacts" in x for x in f)
    assert not any("policy A" in x for x in f)


def test_a_launched_policy_with_no_trace_or_patch_is_not_a_successful_rehearsal():
    """The injected fault: both policies have a launch marker and a record but no trace and
    no patch. The reporter calls that "launched, no usable artifacts"; checking whether ANY
    artifact exists called it success."""
    r = _ok_report()
    for pol in ("A", "B"):
        r["artifacts"]["per_policy"][pol] = _policy_row(usable=False)
    f = RP.rehearsal_failures(r, dry_run=False)
    assert [x for x in f if "policy A left no usable artifacts" in x]
    assert [x for x in f if "policy B left no usable artifacts" in x]
    assert all("trace.jsonl" in x and "patch.diff" in x
               for x in f if "usable artifacts" in x)


def test_a_missing_trace_alone_fails_even_when_the_patch_landed():
    r = _ok_report()
    r["artifacts"]["per_policy"]["A"] = _policy_row(**{"trace.jsonl": False})
    f = RP.rehearsal_failures(r, dry_run=False)
    assert any("missing trace.jsonl" in x for x in f)


def test_a_prompt_that_never_went_out_fails_the_rehearsal():
    r = _ok_report()
    r["outbound_prompts"] = {"per_policy": {"A": {"observed_outbound": True},
                                            "B": {"observed_outbound": False}},
                             "all_registered_prompts_observed": False,
                             "policies_sent_different_prompts": False}
    f = RP.rehearsal_failures(r, dry_run=False)
    assert any("never went out for policy B" in x for x in f)


def test_absent_prompt_verification_is_itself_a_failure():
    """Verification that did not run is not verification that passed."""
    r = _ok_report()
    del r["outbound_prompts"]
    assert any("did not run" in x for x in RP.rehearsal_failures(r, dry_run=False))


def test_both_policies_sending_one_prompt_fails_the_rehearsal():
    r = _ok_report()
    r["outbound_prompts"]["policies_sent_different_prompts"] = False
    assert any("same outbound prompt" in x
               for x in RP.rehearsal_failures(r, dry_run=False))


def test_an_indeterminate_experimental_decision_is_not_a_rehearsal_failure():
    """The rehearsal proves the PATH carries evidence; indeterminate is a legitimate verdict
    about the EXPERIMENT. Failing on it would make the rehearsal refuse a correct run."""
    r = _ok_report(decision={"decision": "indeterminate",
                             "reason": "insufficient decisive evidence"})
    assert RP.rehearsal_failures(r, dry_run=False) == []


def test_a_reporter_exception_fails_the_rehearsal():
    f = RP.rehearsal_failures(_ok_report(decision={"error": "KeyError: 'functional'"}),
                              dry_run=False)
    assert any("reporter failed" in x for x in f)


def test_a_missing_decision_fails_the_rehearsal():
    r = _ok_report()
    del r["decision"]
    assert any("no decision" in x for x in RP.rehearsal_failures(r, dry_run=False))


def test_a_dry_run_is_not_failed_by_absent_artifacts_or_decision():
    """A dry run legitimately writes less and decides nothing; only a runner that actually
    exited non-zero is a failure there."""
    r = {"runner_exit": 0,
         "artifacts": {"per_policy": {"A": {"dir": "/x/A", "record.json": False}}}}
    assert RP.rehearsal_failures(r, dry_run=True) == []
    assert RP.rehearsal_failures({**r, "runner_exit": 9}, dry_run=True)


def test_every_named_failure_is_reported_together():
    r = _ok_report(runner_exit=9, decision={"error": "boom"})
    r["artifacts"]["per_policy"]["B"] = _policy_row(usable=False)
    r["outbound_prompts"]["all_registered_prompts_observed"] = False
    f = RP.rehearsal_failures(r, dry_run=False)
    assert len(f) == 4, f
    assert any("exited 9" in x for x in f)
    assert any("policy B left no usable artifacts" in x for x in f)
    assert any("never went out" in x for x in f)
    assert any("reporter failed" in x for x in f)



def _main_dry_run(monkeypatch, tmp_path, returncode):
    """Drive the REAL `main()` in dry-run with only its external effects substituted.

    `rehearsal_failures` is unit-tested above, but testing the predicate ALONE cannot catch
    a `main` that computes its failures and returns 0 anyway -- which is precisely the shape
    of the defect this file exists to prevent, one layer up. So this exercises the
    connection: real argument parsing, real report assembly, the real call into
    `rehearsal_failures`, and the real return statement.

    Dry-run skips the port checks, the stub and the forwarder outright, so nothing here
    needs a model, a sandbox, a free port or the A2-R fixture trees. It runs on Linux CI.
    """
    monkeypatch.setattr(RP, "prepare_scratch", lambda *a, **k: None)
    monkeypatch.setattr(RP, "artifacts_written_by_production", lambda *a, **k: {
        "per_policy": {"A": {"dir": "/x/A", "record.json": True}},
        "distinct_directories": 1,
        "ledger": {"arm_runs_started": 0, "tokens_known": 0, "unresolved": 0}})
    monkeypatch.setattr(RP.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=returncode, stdout="", stderr=""))
    monkeypatch.setattr(sys, "argv",
                        ["rehearse_a3_production.py", str(tmp_path), "--dry-run"])
    return RP.main()


def test_main_exits_zero_when_the_substituted_runner_succeeds(monkeypatch, tmp_path):
    assert _main_dry_run(monkeypatch, tmp_path, 0) == 0


def test_main_exits_non_zero_when_the_substituted_runner_fails(monkeypatch, tmp_path):
    """A runner exiting 9 must reach the process exit status, not just the report."""
    assert _main_dry_run(monkeypatch, tmp_path, 9) == 1


def test_the_failing_runner_is_named_in_the_report_main_wrote(monkeypatch, tmp_path,
                                                              capsys):
    """The exit status and the stated reason come from the same report, so a non-zero exit
    can never be attributed to a condition the run did not actually observe."""
    assert _main_dry_run(monkeypatch, tmp_path, 9) == 1
    assert "REHEARSAL FAILED" in capsys.readouterr().out


def test_main_is_the_entry_point_the_module_actually_exits_with():
    """`raise SystemExit(main())` -- the return value IS the exit status. If this module is
    ever changed to call `main()` without propagating it, the tests above would keep passing
    while the executable went back to always succeeding."""
    src = inspect.getsource(RP)
    assert "raise SystemExit(main())" in src
