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
    body = src.split("for policy in order:", 1)[1]
    assert "L.fatal_stop(scratch)" in body
    assert body.index("L.fatal_stop(scratch)") < body.index("R.invoke(")


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
