"""A3's execution adapter: one A/B PAIR -- one task, one attempt, two policies.

Thin on purpose. It does not fork `run_arms_isolated`; it imports it and changes the five
things A3 varies, so there is one implementation of the sandbox profile, the environment
gate, the store provisioning and the invocation. §2 holds the runner fixed, and a forked
runner would not be that runner.

    run_a3.py <scratch> <task> <attempt> <schedule-a3.json> [--dry-run]

What it changes, and why each one is necessary:

  1. **the prompt is policy-aware.** `run_arms_isolated` assembles one prompt through
     `task_set.assemble`, which reads a single `consult` block and has no notion of a
     policy. A3's two policies differ in one appended paragraph, so the prompt is assembled
     by `a3_prompts.assemble(task, policy)` and its digest is recorded in the launch marker.
     Computing correct digests in a preflight establishes nothing about what reaches the
     model unless the runner sends that prompt; this is the line that makes it so.
  2. **the layout carries `(task, attempt, policy)`.** `OUT` becomes
     `run-<task>/attempt<n>/policy-<P>`, so the two policies -- which are the SAME ARM --
     cannot land in one directory. The historical layout has that collision built in.
  3. **each policy denies the other.** `boundary_paths` denies sibling ARMS within one
     `OUT`; the sibling here is the other POLICY, one level up. Without this the A arm-run's
     checkout, store copy and trace are readable to B.
  4. **the boundary controls are REQUIRED.** `check_boundary` is called with
     `require=isolation.REQUIRE_A3`, so a shadow control that comes back `None` refuses on
     its own rather than being excluded from `all_hold`.
  5. **the launch is ledgered.** A marker naming the policy and the prompt digest is written
     before the process starts, and a refusal is recorded as a refusal rather than as an
     arm-run that spent nothing.

Fatal stops are checked before EVERY arm-run, including B after A. An admitted pair does not
authorise its second member: if A's consumption cannot be accounted for, or the boundary
refused, B does not launch.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

A3_CONFIG = BENCH / "a3-config-45.json"
ARM = "nexus"                       # both policies run it; the POLICY is not an arm


def _load_runner(scratch: Path, task: str, attempt: int, schedule: Path):
    """Import `run_arms_isolated` with the argv and configuration A3 needs.

    Its globals are computed at import from `sys.argv` and `a1_config.load()`, so they are
    set here rather than patched afterwards -- `RUN`, `FIXTURE_BASE` and `CFG` all derive
    from them, and a half-patched module is worse than a forked one.
    """
    os.environ.setdefault("A1_CONFIG", str(A3_CONFIG))
    sys.argv = ["run_arms_isolated.py", str(scratch), task, str(attempt), str(schedule)]
    import run_arms_isolated as R
    # A rehearsal config is accepted and NAMED. It differs from the frozen one (a private
    # forwarder port), so a row produced under it must never be read as an A3 row.
    if R.CFG.config_version == "a3-v1-REHEARSAL":
        print("run_a3: REHEARSAL configuration -- forwarder port "
              f"{R.CFG.forwarder_port}, NOT the frozen a3-v1. No row produced here is an "
              "A3 result.", flush=True)
    elif R.CFG.config_version != "a3-v1":
        raise SystemExit(f"run_a3: configuration is {R.CFG.config_version!r}, not 'a3-v1'. "
                         f"A3 rows may not be pooled with v2, v3 or A2-R rows, and this "
                         f"runner will not produce one under another configuration.")
    return R


def run_pair(scratch: Path, task: str, attempt: int, schedule: Path,
             dry_run: bool = False) -> int:
    import a3_ledger as L
    import a3_prompts as AP
    import a3_pipeline as P
    import isolation
    import schedule as sched

    R = _load_runner(scratch, task, attempt, schedule)
    plan = sched.load(schedule)
    if sorted(plan.arms) != ["A", "B"]:
        raise SystemExit(f"run_a3: schedule registers arms {plan.arms}; A3's schedule is "
                         f"over POLICIES and must register exactly A,B")
    order = plan.order_for(task, attempt)
    print(f"schedule {schedule.name} digest={plan.schedule_digest[:16]} seed={plan.seed}\n"
          f"{task} attempt {attempt}: realised policy order {' -> '.join(order)}\n")

    allfx = json.loads((R.RUN / "base" / "fixtures.json").read_text())
    fixtures = next(f for f in allfx if f["task"] == task)   # THIS task's own fixture
    checks, held = fixtures["checks"], Path(fixtures["held_checks"])

    # One arm, named once. `ARMS` drives tools, store provisioning and sibling denial; A3's
    # sibling is the other policy, handled below.
    R.ARMS = {ARM: dict(R.ARMS[ARM])}
    _boundary_paths = R.boundary_paths
    records = []

    for policy in order:
        out = P.policy_run_dir(scratch, task, attempt, policy)
        other = P.policy_run_dir(scratch, task, attempt,
                                 "B" if policy == "A" else "A")
        key = f"{task}/{policy}/{attempt}"

        # FATAL STOP, before every arm-run. B is not authorised by the pair's admission.
        stop = L.fatal_stop(scratch)
        if stop:
            print(f"[{policy}] STOP before launch: {stop['reason']}", flush=True)
            return 3

        R.OUT = out
        out.mkdir(parents=True, exist_ok=True)
        R.PROMPT = AP.assemble(task, policy)
        digest = AP.digest(task, policy)

        def paths(arm: str, cwd: Path, _other=other) -> tuple[list[Path], list[Path]]:
            """The sibling POLICY is denied. `boundary_paths` denies sibling arms inside one
            OUT; here the sibling is a peer directory, so it has to be named."""
            allow, deny = _boundary_paths(arm, cwd)
            return allow, [*deny, _other]

        R.boundary_paths = paths

        cwd = R.prepare(ARM)
        store = R.provision_store(ARM)
        profile = R.write_arm_profile(ARM, cwd)
        _, deny = paths(ARM, cwd)

        bound = isolation.check_boundary(profile, cwd, Path(R.FIXTURE_BASE) / "checks" / task,
                                         R.PYTEST_PY, deny, R.BENCH,
                                         require=isolation.REQUIRE_A3)
        print(f"[{policy}] boundary negative: {bound['negative_controls']}", flush=True)
        print(f"[{policy}] boundary required: unresolved={bound['required_unresolved']} "
              f"failed={bound['required_failed']}", flush=True)
        if not bound["all_hold"]:
            L.mark_refusal(scratch, task, attempt, policy, "boundary not demonstrated",
                           {"required_unresolved": bound["required_unresolved"],
                            "required_failed": bound["required_failed"],
                            "negative": bound["negative_controls"],
                            "positive": bound["positive_controls"]})
            print(f"[{policy}] REFUSED: boundary not demonstrated. Nothing was spent.",
                  flush=True)
            return 3

        if dry_run:
            print(f"[{policy}] dry run: prepared, boundary green, prompt {digest}. "
                  f"Not launching.", flush=True)
            records.append({"policy": policy, "prompt_digest": digest, "dry_run": True})
            continue

        # THE LEDGER MARKER, before the process starts. It names the policy and the prompt
        # digest, so an interrupted row can be attributed to a cell and to a prompt.
        L.mark_launch(scratch, task, attempt, policy, identity=R.run_identity(),
                      max_turns=R.MAX_TURNS, wall_clock_s=R.WALL_CLOCK_S,
                      prompt_digest=digest)
        print(f"[{policy}] running, prompt {digest} ...", flush=True)

        rec = R.invoke(ARM, cwd)
        rec["policy"] = policy
        rec["prompt_digest"] = digest
        rec["boundary"] = bound
        rec.update(R.read_trace(ARM))
        patch = R.patch_of(cwd)
        (out / "arms" / ARM / "patch.diff").write_text(patch)
        rec["patch_bytes"] = len(patch)
        rec["scored"] = R.score(cwd, held, checks, R.PYTEST_PY, out / "scoring" / ARM)
        res = rec.get("result") or {}
        u = res.get("usage", {})
        rec["terminal"] = R.classify(rec)
        rec["usage"] = {k: u.get(k) for k in
                        ("input_tokens", "output_tokens", "cache_read_input_tokens",
                         "cache_creation_input_tokens")}
        rec["permission_denials"] = res.get("permission_denials")
        if store:
            rec["store"] = {"path": str(store), "digest_after": R.store_digest(store)}
        # Persisted the moment this arm-run finishes, before the next one starts.
        (out / "arms" / ARM / "record.json").write_text(json.dumps(
            {"task": task, "attempt": attempt, "policy": policy, "arm": ARM,
             "prompt_digest": digest, "identity": R.run_identity(), "record": rec},
            indent=1))
        records.append(rec)
        print(f"  -> {rec['terminal']}  patch={rec['patch_bytes']}B  "
              f"checks: {rec['scored']['summary']}", flush=True)

    pair_dir = P.policy_run_dir(scratch, task, attempt, order[0]).parent
    (pair_dir / "records.json").write_text(json.dumps(
        {"task": task, "attempt": attempt, "order": order, "seed": plan.seed,
         "schedule_digest": plan.schedule_digest, "config": R.CFG.as_recorded(),
         "max_turns": R.MAX_TURNS, "wall_clock_s": R.WALL_CLOCK_S,
         "prompt_digests": {p: AP.digest(task, p) for p in ("A", "B")},
         "corpus_digest_registered": R.CFG.corpus_digest or None,
         "records": records}, indent=1))
    led = L.read(scratch)
    print(f"\nledger: {len(led.started)} started, {led.tokens_known:,} tokens known, "
          f"{len(led.unresolved)} unresolved")
    return 0 if not led.unresolved else 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scratch")
    ap.add_argument("task")
    ap.add_argument("attempt", type=int)
    ap.add_argument("schedule")
    ap.add_argument("--dry-run", action="store_true",
                    help="prepare, gate and check the boundary; do not launch the model")
    a = ap.parse_args()
    return run_pair(Path(a.scratch), a.task, a.attempt, Path(a.schedule), a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
