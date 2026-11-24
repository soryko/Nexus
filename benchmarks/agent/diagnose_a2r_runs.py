"""The three failures and the four passing-but-truncated arm-runs, described from the records.

`CLOSEOUT-a2r.md` asks one bounded question of each group:

  failures                    why did no final source diff remain?
  passing, ended at the cap   which required or optional work was still outstanding?

Model-free. It re-reads saved records and re-uses `diagnose_workflow.describe`, which already
carries the four limits that matter and is already counterexampled:

    issue order is not execution order; an errored command may still have written; a passing
    patch does not date itself; a completion message is not a verification.

A fifth is added here, because this pass reads INTERMEDIATE states and the last one does not
cover it: a patch is a final state, so "the fix was in place at call n" is only ever said
where a command at call n printed something that shows it.

Why not `diagnose_workflow.py <scratch>` directly: its identity gate reads LAUNCH-A2.md
through `report_calibration.parse_launch_record`, which is the v2 calibration's authority and
cannot parse this sweep's record -- A2-R writes `corpus_digest` with a trailing note and its
prompt table carries the v2 digests beside the v3 ones. Rather than loosen a parser whose job
is to be strict about identity, this reads LAUNCH-A2R.md's own Identifiers table and asserts
every record against it. The launch record stays the one authority either way.

    python3 diagnose_a2r_runs.py <scratch> [--launch <LAUNCH-A2R.md>] [--json <out>]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import diagnose_workflow as DW                                          # noqa: E402

DEFAULT_LAUNCH = BENCH.parent.parent / "LAUNCH-A2R.md"

#: The groups the closeout asks about. Not derived from the outcomes -- named here so the
#: report cannot quietly re-cut them once the numbers are in view.
FAILURES = [("k3", "nexus"), ("k4", "baseline"), ("k4", "notes")]
PASS_TRUNCATED = [("k1", "nexus"), ("k3", "baseline"), ("k3", "notes"), ("k4", "nexus")]


def read_identity(path: Path) -> dict:
    """The frozen identity, from A2-R's own launch record.

    `| `field` | `value` |` with an optional trailing note after the value, and the ceiling
    row `| 45 | `<file>` | `<digest>` | <max_turns> |`. The prompt table carries two digest
    columns, v2 and v3; the LAST is this sweep's, and the header row is checked to say so
    rather than assumed.
    """
    text = path.read_text()
    out: dict = {"prompts": {}}
    for row in re.finditer(r"^\|\s*`([a-z_]+)`\s*\|\s*`([^`]+)`.*\|\s*$", text, re.M):
        out[row.group(1)] = row.group(2)
    for row in re.finditer(r"^\|\s*(\d+)\s*\|\s*`[^`]+`\s*\|\s*`([0-9a-f]+)`\s*\|\s*(\d+)\s*\|",
                           text, re.M):
        out["ceiling"] = int(row.group(1))
        out["config_digest"] = row.group(2)
        out["max_turns"] = int(row.group(3))
    if not re.search(r"^\|\s*task\s*\|.*v2.*\|.*\*\*v3[^|]*\*\*\s*\|\s*$", text, re.M):
        raise ValueError(f"{path}: prompt table header does not put v3 last; "
                         f"which column is this sweep's cannot be guessed at")
    for row in re.finditer(r"^\|\s*(k\d)\s*\|\s*`[0-9a-f]+`\s*\|\s*\*\*`([0-9a-f]+)`\*\*\s*\|",
                           text, re.M):
        out["prompts"][row.group(1)] = row.group(2)
    need = ("product_revision", "harness_revision", "config_version", "corpus_digest",
            "schedule_digest", "config_digest", "max_turns")
    missing = [f for f in need if not out.get(f)]
    if missing or len(out["prompts"]) != 4:
        raise ValueError(f"{path}: launch record is missing {missing or 'its prompt table'}; "
                         f"it is the authority on identity and cannot be guessed at")
    return out


def mismatches(ident: dict | None, task: str, want: dict) -> list[str]:
    if not ident:
        return ["records no identity at all"]
    pairs = [("config_version", want["config_version"]),
             ("product_revision", want["product_revision"]),
             ("harness_revision", want["harness_revision"]),
             ("config_digest", want["config_digest"]),
             ("corpus_digest_registered", want["corpus_digest"]),
             ("max_turns_applied", want["max_turns"]),
             ("prompt_digest", want["prompts"].get(task))]
    return [f"{f}={ident.get(f)!r} != {v!r}" for f, v in pairs if ident.get(f) != v]


def build(scratch: Path, launch: Path) -> dict:
    want = read_identity(launch)
    rows, refused = [], []
    for rec in sorted(scratch.glob("*/run-*/attempt*/records.json")):
        data = json.loads(rec.read_text())
        ident, task = data.get("identity"), data.get("task")
        if why := mismatches(ident, task, want):
            refused.append({"records": str(rec), "task": task, "why": why})
            continue
        for r in data.get("records", []):
            if not (r.get("terminal") or {}).get("scored"):
                continue
            rows.append(DW.describe(rec, data, r, want["max_turns"]))
    rows.sort(key=lambda x: (x["task"], x["arm"]))
    return {"identity": want, "rows": rows, "refused": refused}


def timeline(scratch: Path, task: str, arm: str, limit: int = 0) -> list[dict]:
    """One compact event line per tool call, from the saved record."""
    rec = json.loads((scratch / f"c45/run-{task}/attempt1/arms/{arm}/record.json").read_text())
    out = []
    for c in rec["record"]["tool_calls"]:
        name = c.get("name") or ""
        inp = c.get("input") or {}
        cmd = inp.get("command") or inp.get("file_path") or ""
        res = " ".join(str(c.get("result") or "").split())
        kind = DW.classify(c, arm) if hasattr(DW, "classify") else None
        out.append({"index": c["index"], "tool": name, "error": bool(c.get("is_error")),
                    "what": " ".join(str(cmd).split())[:200],
                    "result_tail": res[-200:], "kind": kind})
    return out[:limit] if limit else out


def render(rep: dict) -> str:
    L: list[str] = []
    bar = "=" * 110
    L += [bar, "A2-R -- FAILURES AND CEILING-TRUNCATED PASSES (descriptive; saved records; "
          "no model invoked)", bar,
          "Issue order, not execution order. An errored command may still have written.",
          "A patch is a FINAL state: when it first became correct is not inferred.",
          "A completion message is a statement the model made, not a verification.", ""]
    if rep["refused"]:
        L.append(f"REFUSED {len(rep['refused'])} records file(s) on identity:")
        for r in rep["refused"]:
            L.append(f"    {r['task']}: {'; '.join(r['why'])}")
        L.append("")
    by = {(r["task"], r["arm"]): r for r in rep["rows"]}
    hdr = (f"  {'cell':<14}{'calls':>6}{'srcEd':>6}{'last':>6}{'after':>6}{'tEdit':>6}"
           f"{'pytest':>7}{'t.aft':>6}{'repro':>6}{'retr':>5}{'reread':>7}{'gitm':>5}"
           f"{'err':>4}{'wall':>7}")

    def block(title: str, cells: list[tuple[str, str]]) -> None:
        L.append(title)
        L.append(hdr)
        L.append("  " + "-" * (len(hdr) - 2))
        for c in cells:
            r = by.get(c)
            if not r:
                L.append(f"  {c[0]}/{c[1]}: NOT PRESENT")
                continue
            a = r["after_last_src_edit"] or {}
            L.append(f"  {c[0]+'/'+c[1]:<14}{r['tool_calls']:>6}"
                     f"{len(r['src_edit_calls']):>6}{str(r['last_src_edit_index']):>6}"
                     f"{str(r['calls_after_last_src_edit']):>6}{r['test_edit_calls']:>6}"
                     f"{r['test_attempt_count']:>7}{str(a.get('test_attempts', '-')):>6}"
                     f"{r['repro_run_count']:>6}{r['retrieval_calls']:>5}"
                     f"{r['re_read_calls']:>7}{r['git_src_mutation_calls']:>5}"
                     f"{r['obstruction']['errored_commands']:>4}{r['wall_clock_s']:>7}")
        L.append("")

    block("(1) FUNCTIONAL FAILURES -- no final source diff", FAILURES)
    block("(2) FUNCTIONAL PASSES THAT ENDED AT THE CEILING", PASS_TRUNCATED)
    block("(3) COMPLETED PASSES -- descriptive context, NOT a control",
          [(t, a) for t in ("k1", "k2", "k3", "k4") for a in ("baseline", "nexus", "notes")
           if (t, a) not in FAILURES and (t, a) not in PASS_TRUNCATED])
    L += ["  srcEd = identified source-EDIT events; last = index of the last one;",
          "  after = calls issued after it; t.aft = pytest invocations after it;",
          "  gitm = git operations that CHANGE source state (stash/checkout) -- not edits.", ""]
    return "\n".join(L)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    scratch = Path(argv[1])
    launch = (Path(argv[argv.index("--launch") + 1]) if "--launch" in argv else DEFAULT_LAUNCH)
    rep = build(scratch, launch)
    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        out.write_text(json.dumps(rep, indent=1) + "\n")
        print(f"wrote {out}", file=sys.stderr)
    print(render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
