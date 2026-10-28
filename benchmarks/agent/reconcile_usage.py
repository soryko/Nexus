"""Recover what a trace can say about an arm-run whose terminal envelope never arrived.

One arm-run in the v2 sweep -- `c60/run-k1/attempt1/arms/baseline` -- was killed at the 600 s
wall clock. The runner buffers a trace until the subprocess returns, and this time the buffer
survived: 16 610 lines, 81 assistant messages, and a usage block of four nulls, because the
envelope that carries the terminal usage was never emitted. §4 calls that `unresolved`, and
unresolved BLOCKS -- it is never zero.

This does not resolve it. It measures what the trace supports, so that any allowance written
later rests on a figure somebody checked rather than on the first number that could be summed.

**The obvious sum is wrong, and wrong in the expensive direction.** Assistant messages repeat:
the 81 messages in that trace carry 35 distinct message IDs, and a repeated ID repeats the same
usage block rather than reporting more consumption. Summing all 81 counts most requests two to
four times. On this trace it inflates the figure by 2.28x.

So the method is: **one usage per distinct message ID**, and then -- the part that makes it
evidence rather than another guess -- the same method run against every arm-run that DID
produce an envelope, and compared with it. That is a negative control on the extraction: if
deduplicating by ID did not reproduce a known envelope, it would not be trusted to stand in
for a missing one.

What that comparison establishes on this sweep, and its two limits, are in the closeout. Both
limits are properties of the instrument, not of the arithmetic:

  output_tokens   is 0 in all 3 226 assistant messages across every trace in the scratch,
                  while every envelope reports a real figure. The forwarder does not report
                  output per message. Output is recoverable ONLY from an envelope, so for an
                  arm-run without one it is unobserved -- not zero.
  the last request a trace can only carry what was written before the kill. One completed
                  arm-run (c45/k3/baseline) is already short of its own envelope by 39 770
                  tokens, so this method is a LOWER BOUND even where it matches.

Usage:  reconcile_usage.py <scratch> [--arm <path to an arm directory>] [--json <out.json>]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import run_calibration as RC                                            # noqa: E402

FIELDS = RC.TOKEN_FIELDS
# The two fields the per-message usage blocks actually carry on this forwarder. The
# validation below compares only these, because comparing output_tokens would compare a
# figure the trace never reports against one the envelope always does, and report a
# "mismatch" on every single arm-run.
OBSERVED = ("input_tokens", "cache_read_input_tokens")


def scan_trace(trace: Path) -> dict:
    """-> naive sum, per-ID sum, the terminal envelope's usage, and the ID conflicts.

    `conflicts` is not decoration: the provider's own documentation warns that two messages
    sharing an ID may report DIFFERENT output-token counts, in which case "one usage per ID"
    would be choosing between two figures rather than dropping a duplicate. On this sweep
    the count is zero -- every repeat is byte-identical -- and that is a measured property of
    these traces, not a guarantee about traces in general.
    """
    naive = dict.fromkeys(FIELDS, 0)
    by_id: dict[str, dict] = {}
    conflicts: list[dict] = []
    envelope = None
    messages = 0

    for line in trace.open(errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") == "result":
            envelope = rec                       # last one wins; the terminal envelope
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        usage = msg.get("usage") or {}
        if not usage:
            continue
        messages += 1
        vals = {k: (usage.get(k) or 0) for k in FIELDS}
        for k in FIELDS:
            naive[k] += vals[k]
        mid = msg.get("id")
        if mid in by_id:
            if by_id[mid] != vals:
                conflicts.append({"id": mid, "first": by_id[mid], "later": vals})
        else:
            by_id[mid] = vals

    dedup = {k: sum(v[k] for v in by_id.values()) for k in FIELDS}
    env_usage = (envelope or {}).get("usage")
    return {"trace": str(trace), "assistant_messages": messages,
            "distinct_message_ids": len(by_id),
            "naive_sum": naive, "naive_total": sum(naive.values()),
            "dedup_by_id": dedup, "dedup_total": sum(dedup.values()),
            "conflicting_ids": conflicts,
            "envelope_usage": ({k: (env_usage.get(k) or 0) for k in FIELDS}
                               if env_usage else None)}


def validate(scans: list[dict]) -> dict:
    """The negative control: does one-usage-per-ID reproduce the envelopes we already have?

    **A match on nothing is not a match.** 36 of the traces in this scratch are the
    forwarder-down rows: no assistant message ever arrived and the envelope reports an honest
    zero, so `0 == 0` and the comparison "passes" without having compared anything. Counting
    those among the successes would inflate the control from 26 real agreements to 62 and
    make a broken extractor look validated. They are counted as `vacuous` and kept out of the
    figure the conclusion rests on.
    """
    exact, vacuous, mismatched, no_envelope = [], [], [], []
    for s in scans:
        env = s["envelope_usage"]
        if env is None:
            no_envelope.append(s)
            continue
        # Vacuity is "nothing was compared", not "no message arrived". The forwarder-down
        # rows DO carry one assistant message each -- with a usage block of zeros, against
        # an envelope of zeros. The test is on both sides being empty of quantity.
        if not any(env.values()) and not any(s["dedup_by_id"].values()):
            vacuous.append(s)
            continue
        if all(s["dedup_by_id"][k] == env[k] for k in OBSERVED):
            exact.append(s)
        else:
            mismatched.append({**s, "shortfall": {k: env[k] - s["dedup_by_id"][k]
                                                  for k in OBSERVED}})
    return {"exact": exact, "vacuous": vacuous, "mismatched": mismatched,
            "without_envelope": no_envelope}


def build(scratch: Path, only: Path | None = None) -> dict:
    traces = ([only / "trace.jsonl"] if only else
              sorted(scratch.glob("*/run-*/attempt*/arms/*/trace.jsonl")))
    scans = [scan_trace(t) for t in traces if t.exists()]
    v = validate(scans)
    out_seen = {s["trace"]: s["naive_sum"]["output_tokens"] for s in scans}
    return {"scans": scans, "validation": {
        "traces": len(scans), "exact_on_observed_fields": len(v["exact"]),
        "vacuous_nothing_to_compare": len(v["vacuous"]),
        "mismatched": [{"trace": m["trace"], "shortfall": m["shortfall"],
                        "shortfall_total": sum(m["shortfall"].values())}
                       for m in v["mismatched"]],
        "without_envelope": [s["trace"] for s in v["without_envelope"]],
        "output_tokens_reported_in_any_message": any(out_seen.values()),
        "total_conflicting_ids": sum(len(s["conflicting_ids"]) for s in scans)}}


def render(rep: dict) -> str:
    out: list[str] = []
    w = out.append
    v = rep["validation"]
    w("=" * 96)
    w("TRACE USAGE RECONCILIATION -- one usage per distinct message ID")
    w("=" * 96)
    w("  The control below is what licenses the reconstruction: the same extraction, run "
      "against")
    w("  arm-runs whose true total is already known from an envelope.")
    w(f"  traces scanned                         {v['traces']}")
    w(f"  vacuous (no messages, zero envelope)   {v['vacuous_nothing_to_compare']} "
      f"-- excluded from the control, they compare nothing")
    w(f"  reproduce their envelope exactly       {v['exact_on_observed_fields']} "
      f"(on {', '.join(OBSERVED)})")
    w(f"  short of their envelope                {len(v['mismatched'])}")
    for m in v["mismatched"]:
        w(f"      {m['trace']}")
        w(f"      short by {m['shortfall_total']:,} tokens {m['shortfall']}")
    w(f"  with no envelope to compare against    {len(v['without_envelope'])}")
    w(f"  messages sharing an ID but disagreeing {v['total_conflicting_ids']}")
    w(f"  any message reporting output_tokens    {v['output_tokens_reported_in_any_message']}")
    w("")
    for s in rep["scans"]:
        if s["envelope_usage"] is not None and len(rep["scans"]) > 1:
            continue
        w(f"  {s['trace']}")
        w(f"      assistant messages {s['assistant_messages']}, "
          f"distinct IDs {s['distinct_message_ids']}")
        w(f"      naive sum over every message  {s['naive_total']:>12,}   <- OVER-COUNTS")
        w(f"      one usage per message ID      {s['dedup_total']:>12,}")
        w(f"      of which input_tokens         {s['dedup_by_id']['input_tokens']:>12,}")
        w(f"      of which cache_read           "
          f"{s['dedup_by_id']['cache_read_input_tokens']:>12,}")
        w(f"      output_tokens                      unobserved "
          f"(the forwarder reports none per message)")
        if s["envelope_usage"] is None:
            w("      NO TERMINAL ENVELOPE: this arm-run stays unresolved. The figure above is")
            w("      a reconstruction and a lower bound, not a measurement, and it is not")
            w("      written into any record.")
    w("=" * 96)
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    only = Path(argv[argv.index("--arm") + 1]) if "--arm" in argv else None
    rep = build(Path(argv[1]), only)
    print(render(rep))
    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        out.write_text(json.dumps(rep, indent=1) + "\n")
        print(f"\nfull record -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
