"""Rehearse the REAL A3 execution path, with only the model endpoint replaced.

`rehearse_a3.py` writes the artifacts itself. That validates everything DOWNSTREAM of the
records and nothing about the thing that produces them, so it cannot establish that the
runner emits what the reporter consumes, nor that the prompt whose digest a preflight froze
is the prompt that reaches the model.

This drives `run_a3.py` -- the production adapter -- against the real fixture trees, the real
sandbox profiles, the real environment gate, the real boundary controls and the real
compliance scorer. The ONLY substitution is the model endpoint: the forwarder is pointed at
`stub_turns.py` over loopback, exactly as `A1_FORWARDER_STUB_UPSTREAM` allows, so the arm
runs a real agent loop against an upstream that costs nothing and never leaves the host.

    python3 rehearse_a3_production.py <scratch> [--task k1] [--attempt 1] [--dry-run]

Three things it establishes that the downstream rehearsal cannot:

  1. **the outbound prompt is the registered prompt.** The stub records a digest of every
     outbound user text; the registered digest for `(task, policy)` must appear among them.
     A preflight that computes digests proves nothing about what is sent unless something
     observes what is sent.
  2. **the production writer produces the artifacts.** `record.json`, `patch.diff`,
     `trace.jsonl` and the launch marker are written by `run_a3.py`/`run_arms_isolated`, not
     by `write_arm_run`, and the reporter is then run over them.
  3. **the boundary is required.** `run_a3` passes `require=isolation.REQUIRE_A3`, so this
     path refuses where the historical runner would have continued.

`--dry-run` stops after the boundary check, before the model is invoked. It is the cheap
form and needs no stub at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a3_prompts as AP                                                    # noqa: E402

STUB_PORT = 8917
#: NOT 8899. The production forwarder's port is 8899, and on 2026-09-20 this script used it:
#: a forwarder started on 2026-09-15 was still listening there, pointed at the REAL upstream.
#: `_listening(8899)` saw that listener, reported success, and the rehearsal ran two arm-runs
#: against the real model -- 2 669 285 tokens of unauthorised paid execution, with paid
#: execution explicitly stopped. The forwarder this script started never bound at all and
#: died unobserved.
#:
#: Four defects, and any ONE of them would have prevented it:
#:   1. the rehearsal used the production port instead of a private one;
#:   2. it treated "something is listening" as "my process is listening";
#:   3. it never checked that the process it started was still alive;
#:   4. `setdefault` left a real DEEPSEEK_API_KEY in the child environment.
#: All four are closed below, and `assert_stub_upstream` makes the check positive: the
#: rehearsal proves it is talking to ITS OWN stub before any arm-run is launched.
FORWARDER_PORT = 8918
FIXTURE_SOURCE = Path("/Users/soko/Cerebros/nexus-a1-fixtures/a2r-run/c45")

#: Never a real key. The stub upstream ignores it; a real forwarder would not.
STUB_KEY = "stub-key-this-rehearsal-must-never-reach-a-paid-endpoint"


class NotTheStub(RuntimeError):
    """Raised rather than launching an arm-run whose egress is not this script's stub."""


def _port_free(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), 0.5).close()
        return False
    except OSError:
        return True


def _listening(port: int, timeout: float = 15.0, proc=None) -> bool:
    """Wait for OUR process to listen. `proc` dying is a failure, not a slow start."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            socket.create_connection(("127.0.0.1", port), 1).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def assert_stub_upstream(port: int, token: str) -> None:
    """Prove, BEFORE any arm-run, that this port reaches THIS script's stub instance.

    A POSITIVE control, and it has to come back through the forwarder: the stub's log is
    only written on SIGTERM, so mid-run there is nothing on disk to read -- the first
    version of this check looked for that file and refused every time, which was the right
    verdict for the wrong reason.

    So the stub echoes a per-run token to any non-loop request, and this sends one through
    the forwarder and requires the token in the reply. A forwarder pointed anywhere else --
    at another stub, or at the real upstream -- cannot produce it. Only the path that is
    about to be spent against is exercised, which is the whole point: on 2026-09-20 the port
    was reached, the listener answered, and it was a five-day-old forwarder aimed at a paid
    endpoint.
    """
    import urllib.request
    body = json.dumps({"model": "probe", "max_tokens": 1, "stream": True,
                       "messages": [{"role": "user", "content": "upstream probe"}]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/anthropic/v1/messages",
                                 data=body, method="POST",
                                 headers={"content-type": "application/json",
                                          "x-api-key": STUB_KEY})
    try:
        got = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    except Exception as exc:
        raise NotTheStub(
            f"the upstream control through :{port} failed ({type(exc).__name__}: {exc}). "
            f"REFUSING to launch: an arm-run whose egress is not this rehearsal's stub is "
            f"paid execution.") from exc
    if token not in got:
        raise NotTheStub(
            f"the upstream control through :{port} did NOT come back from this rehearsal's "
            f"stub -- the instance token is absent from the reply. Something else is "
            f"answering on that port. REFUSING to launch: this is exactly how 2 669 285 "
            f"tokens were spent on 2026-09-20.")
    print(f"  upstream control: the reply carries this run's stub token -- egress "
          f"confirmed\n", flush=True)


def rehearsal_config(scratch: Path) -> Path:
    """A3's configuration with the forwarder port moved, written into the scratch.

    `forwarder_port` comes from the CONFIG, not the environment, so a rehearsal on a private
    port has to say so in a config -- setting an environment variable does nothing and the
    runner would have gone on using 8899. That is the port the production forwarder holds,
    and on 2026-09-20 it held a forwarder aimed at the real upstream.

    This config is DELIBERATELY NOT the launch config, and its digest differs. It is written
    into the scratch, never beside `a3-config-45.json`, so it cannot be mistaken for the
    frozen one or picked up by a preflight.
    """
    cfg = json.loads((BENCH / "a3-config-45.json").read_text())
    cfg["forwarder_port"] = FORWARDER_PORT
    cfg["config_version"] = "a3-v1-REHEARSAL"
    out = scratch / "rehearsal-config.json"
    out.write_text(json.dumps(cfg, indent=1) + "\n")
    return out


def prepare_scratch(scratch: Path, task: str, source: Path = FIXTURE_SOURCE) -> Path:
    """A private copy of the task's own fixture tree. The real one, not a synthetic repo."""
    src = source / f"run-{task}" / "base"
    if not src.is_dir():
        raise SystemExit(f"no fixture base at {src}; this rehearsal uses the A2-R sweep's "
                         f"own prepared trees and cannot invent one")
    dst = scratch / f"run-{task}" / "base"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, symlinks=True)
    return dst


def verify_outbound_prompts(stub_log: Path, task: str,
                            policies: tuple[str, ...] = ("A", "B")) -> dict:
    """Did the registered prompt for each policy actually go out?

    The check is over DIGESTS the stub computed from the request bodies it received, against
    digests `a3_prompts` computes from the registration. Nothing is copied between the two
    sides.
    """
    log = json.loads(stub_log.read_text())
    seen: set[str] = set()
    for r in log.get("requests", []):
        seen.update(r.get("user_text_digests") or [])
    out = {"requests_received": log.get("requests_received"),
           "scripted_turns_served": log.get("scripted_turns_served"),
           "distinct_user_text_digests": len(seen), "per_policy": {}}
    for p in policies:
        want = AP.digest(task, p)
        out["per_policy"][p] = {"registered_digest": want, "observed_outbound": want in seen}
    out["all_registered_prompts_observed"] = all(
        v["observed_outbound"] for v in out["per_policy"].values())
    # A and B must be DIFFERENT outbound prompts, or the adapter sent one policy twice.
    out["policies_sent_different_prompts"] = (
        len({AP.digest(task, p) for p in policies}) == len(policies)
        and all(v["observed_outbound"] for v in out["per_policy"].values()))
    return out


def artifacts_written_by_production(scratch: Path, task: str, attempt: int,
                                    policies: tuple[str, ...] = ("A", "B")) -> dict:
    """What the PRODUCTION writer left behind, per (task, attempt, policy)."""
    import a3_pipeline as P
    import a3_ledger as L
    rows = {}
    for p in policies:
        d = P.arm_run_dir(scratch, task, attempt, p)
        rows[p] = {"dir": str(d),
                   **{f: (d / f).is_file() for f in
                      ("launched.json", "a3-launch.json", "trace.jsonl", "patch.diff",
                       "record.json", "sandbox.sb", "envcheck.json")}}
    led = L.read(scratch)
    return {"per_policy": rows, "ledger": led.as_dict(),
            "distinct_directories": len({r["dir"] for r in rows.values()})}


def report_over_production(scratch: Path, task: str, attempt: int) -> dict:
    """Run the PRODUCTION artifacts through the decision reporter. Model-free.

    Checking that the files exist and that the prompts went out is not the same as checking
    that the reporter can read them. It could not: the production writer recorded the
    functional result inside `record.json` while `normalise` looked for `functional.json`,
    so every production row arrived at the decision table as `functional_pass: None` --
    unknown -- with the scorer having settled it. Nothing in an artifact-presence check sees
    that, because the artifact was present.

    The plan here is narrowed to the ONE pair that ran. Against the full 16-cell plan the
    report would be dominated by missing coverage and the rows that did run would not be
    legible, which is a property of rehearsing one pair rather than a finding.
    """
    import a3_pipeline as P
    from a3_decision import Plan, render

    corpus = json.loads((BENCH / "corpus-dev-m1.json").read_text())
    bodies = {m["id"]: m["content"] for m in corpus["memories"]}
    facts = P.registered_facts()
    plan = Plan(tasks=(task,), policies=("A", "B"), attempts=attempt)
    pristine = scratch / f"run-{task}" / "base" / task
    cfg = json.loads((BENCH / "a3-config-45.json").read_text())

    rep = P.report(scratch, facts, plan, bodies=bodies, pristine=pristine,
                   python=cfg["pytest_python"], notes_file=BENCH / "notes-dev-m1.md")
    print("\n" + render(rep))
    return rep


def rehearsal_failures(report: dict, dry_run: bool) -> list[str]:
    """Everything the rehearsal OBSERVED that means it did not rehearse successfully.

    Each condition below was already recorded in the report and then thrown away by an
    unconditional `return 0`: a runner exiting non-zero, a policy that wrote no artifacts,
    and a reporter that raised were each printed as prose and exited 0, so CI and every
    caller read a failed rehearsal as green. A rehearsal that cannot fail catches nothing.
    """
    failures: list[str] = []
    if report.get("runner_exit"):
        failures.append(f"production runner exited {report['runner_exit']}")
    if not dry_run:
        for pol, row in (report.get("artifacts") or {}).get("per_policy", {}).items():
            if not [k for k, v in row.items() if k != "dir" and v]:
                failures.append(f"policy {pol} wrote no artifacts")
    dec = report.get("decision") or {}
    if "error" in dec:
        failures.append(f"reporter failed: {dec['error']}")
    elif not dry_run and "decision" not in dec:
        failures.append("reporter produced no decision")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scratch")
    ap.add_argument("--task", default="k1")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--script", default="a3_consult_edit_test")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    scratch = Path(a.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    prepare_scratch(scratch, a.task)
    stub_log = scratch / "stub-requests.json"
    report: dict = {"task": a.task, "attempt": a.attempt, "dry_run": a.dry_run,
                    "scratch": str(scratch)}

    stub = fwd = None
    try:
        if not a.dry_run:
            # Both ports must be FREE. Binding is what proves the listener is ours, and a
            # port already in use is the exact condition that sent two arm-runs to a
            # five-day-old forwarder aimed at the real upstream.
            for port, what in ((STUB_PORT, "stub"), (FORWARDER_PORT, "forwarder")):
                if not _port_free(port):
                    raise SystemExit(
                        f"port {port} is already in use, so the {what} this rehearsal "
                        f"starts would not be the one the arms reach. REFUSING: this is "
                        f"how unauthorised paid execution happened on 2026-09-20.")
            token = f"STUB-INSTANCE-{uuid.uuid4().hex[:16]}"
            stub = subprocess.Popen(
                [sys.executable, str(BENCH / "stub_turns.py"), "--port", str(STUB_PORT),
                 "--script", a.script, "--log", str(stub_log),
                 "--instance-token", token],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if not _listening(STUB_PORT, proc=stub):
                raise SystemExit("stub_turns did not come up")
            env = {**os.environ,
                   "A1_FORWARDER_STUB_UPSTREAM": f"127.0.0.1:{STUB_PORT}"}
            fwd = subprocess.Popen(
                [sys.executable, str(BENCH / "model_forwarder.py"), str(FORWARDER_PORT)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if not _listening(FORWARDER_PORT, proc=fwd):
                raise SystemExit(
                    "the forwarder this rehearsal started did not come up (it may have "
                    "failed to bind). REFUSING to launch against a listener that is not "
                    "ours.")
            print(f"stub on :{STUB_PORT} (script {a.script}), forwarder on "
                  f":{FORWARDER_PORT} -> stub", flush=True)
            # ... and PROVE it, before spending anything.
            assert_stub_upstream(FORWARDER_PORT, token)

        cmd = [sys.executable, str(BENCH / "run_a3.py"), str(scratch), a.task,
               str(a.attempt), str(BENCH / "schedule-a3.json")]
        if a.dry_run:
            cmd.append("--dry-run")
        runner_env = {**os.environ, "A1_CONFIG": str(rehearsal_config(scratch))}
        # OVERWRITE, never setdefault. A real key inherited from the environment is what
        # made the 2026-09-20 arm-runs billable once they reached a real forwarder.
        runner_env["DEEPSEEK_API_KEY"] = STUB_KEY
        # The runner reads the port from the configuration, so a rehearsal on a private port
        # must say so rather than hoping.

        r = subprocess.run(cmd, env=runner_env, text=True, capture_output=True)
        report["runner_exit"] = r.returncode
        report["runner_tail"] = (r.stdout + r.stderr).strip().splitlines()[-25:]
        print(r.stdout[-4000:])
        if r.stderr.strip():
            print(r.stderr[-2000:], file=sys.stderr)
    finally:
        for proc in (fwd, stub):
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

    report["artifacts"] = artifacts_written_by_production(scratch, a.task, a.attempt)
    if not a.dry_run:
        try:
            decided = report_over_production(scratch, a.task, a.attempt)
            report["decision"] = {
                "decision": decided["decision"], "reason": decided["reason"],
                "states": decided["states"],
                "experiment_valid": decided["experiment_valid"],
                "consumption": decided["consumption"],
                "relevance": decided["relevance"],
                "normalisation": decided["normalisation"]}
        except Exception as exc:
            report["decision"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            print(f"\nREPORTER FAILED: {type(exc).__name__}: {exc}")
    if not a.dry_run and stub_log.is_file():
        report["outbound_prompts"] = verify_outbound_prompts(stub_log, a.task)

    print("\n" + "=" * 88)
    art = report["artifacts"]
    for p, row in art["per_policy"].items():
        wrote = [k for k, v in row.items() if k != "dir" and v]
        print(f"  policy {p}: {', '.join(wrote) or 'NOTHING'}")
    print(f"  distinct directories: {art['distinct_directories']}")
    print(f"  ledger: {art['ledger']['arm_runs_started']} started, "
          f"{art['ledger']['tokens_known']:,} known, "
          f"{art['ledger']['unresolved']} unresolved")
    if "outbound_prompts" in report:
        op = report["outbound_prompts"]
        for p, v in op["per_policy"].items():
            print(f"  outbound prompt {p}: registered {v['registered_digest']} "
                  f"observed={v['observed_outbound']}")
        print(f"  all registered prompts observed: "
              f"{op['all_registered_prompts_observed']}")
    d = report.get("decision") or {}
    if "decision" in d:
        n = d["normalisation"]
        print(f"  reporter read {len(n)} production row(s); functional settled in "
              f"{sum(1 for r in n if r.get('functional_source', '').startswith('functional'))}")
        print(f"  decision through the production artifacts: {d['decision'].upper()}")
    failures = rehearsal_failures(report, a.dry_run)
    report["failures"] = failures

    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1, default=str) + "\n")
        print(f"\n-> {a.json}")
    if failures:
        print("\nREHEARSAL FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
