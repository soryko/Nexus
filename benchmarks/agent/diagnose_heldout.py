"""A1 held-out diagnostics: where each arm-run spent its effort. No model is invoked.

This is descriptive. It reads the 36 saved traces, the 12 `records.json` files and the 36
saved patches, and answers "what did the run DO" -- not "why did it fail". It can nominate
hypotheses; it cannot establish a cause. Nothing here re-scores the run: functional verdicts
come from the saved `scored` blocks under `a1-functional-2`, unchanged.

Three accounting rules it does not bend:

  * **`num_turns`, tool calls and the turn ceiling are three different numbers.** runner-a1
    measured a cap of 2 producing `num_turns: 3`. They are reported in separate columns and
    never summed or substituted.

  * **Absence of `Edit`/`Write` does not prove absence of mutation.** An agent can rewrite a
    file with `sed -i`, a heredoc redirect, `tee`, `cp`, `patch` or `git apply`, and six of
    these 36 arm-runs did write files through Bash alone. Every Bash call is parsed for a
    write target, and that target is checked against the task's own base tree, so a shell
    edit to tracked source counts as a mutation and a scratch file does not.

  * **Unavailable telemetry is `unknown`, never 0.** A Bash command that writes somewhere
    this parser cannot resolve is counted in `unknown_writes` and says so; it is not silently
    dropped into the "no mutation" bucket.

Usage:  diagnose_heldout.py <run-dir> [click-clone] [--from-records] [--check-sources]
                            [--verify-base-trees] [--json out.json] [--md out.md]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

from trace_parse import parse                                           # noqa: E402

ARMS = ("baseline", "nexus", "notes")
TASKS_FILE = BENCH / "tasks-heldout-a1.json"
NOTES_NAME = "NOTES-FROM-EARLIER-WORK"

# Tools that mutate a named file directly. `file_path` is the input key for all of them.
DIRECT_MUTATORS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

# Redirect noise that is not a write to a file: fd duplication (`2>&1`, `&>`) and the null
# sink. Stripped before anything else looks at the command, because `2>&1` on a read-only
# `pytest ... | tail` otherwise reads as an unplaceable write. It fired on 115 of these 36
# runs' Bash calls, every one of them read-only.
NONWRITE_REDIRECT = re.compile(r"\d*>&\d+|&>>?\s*\S+|\d*>>?\s*/dev/\S+")

# A Bash call is examined for a write target only if one of these appears. The list stays
# broad among real write verbs -- a false candidate costs one `unknown`, a missed one costs a
# wrong "never mutated" verdict, and those two errors are not equally bad. `python -c` is NOT
# here: it is a write only when it opens something for writing, which is checked separately.
WRITE_HINTS = re.compile(
    r"(?:^|[\s;&|(])(?:sed\s+-i|tee\b|patch\b|git\s+apply|cp\b|mv\b|dd\b|truncate\b|"
    r"install\b|perl\s+-i)|>>?\s*\S|<<\s*['\"]?EOF",
    re.MULTILINE,
)

# Redirects and the common in-place editors, each yielding a candidate path.
TARGET_PATTERNS = (
    re.compile(r">>?\s*([^\s;&|>]+)"),                     # cat > f / echo >> f
    re.compile(r"\bsed\s+-i(?:\s+\S+)?\s+(?:-e\s+\S+\s+)*([^\s;&|]+)"),
    re.compile(r"\btee\s+(?:-a\s+)?([^\s;&|]+)"),
    re.compile(r"\bperl\s+-i\S*\s+(?:-\S+\s+)*([^\s;&|]+)"),
    re.compile(r"\bcp\s+(?:-\S+\s+)*\S+\s+([^\s;&|]+)"),
    re.compile(r"\bmv\s+(?:-\S+\s+)*\S+\s+([^\s;&|]+)"),
)

PY_WRITE = re.compile(r"python[0-9.]*\s+-c")
PY_OPEN_W = re.compile(r"open\([^)]*['\"][wa]")

TEST_RUN = re.compile(r"\b(?:pytest|tox|unittest|nox)\b")


BASE_TREES = BENCH / "base-trees-heldout-a1.json"


def tracked_paths(clone: Path | None, task: str, commit: str) -> set[str]:
    """Every path tracked in the task's own base tree, as repo-relative strings.

    Read from the committed `base-trees-heldout-a1.json` when it is present, so the rebuild
    needs no clone of `pallets/click` and no network. `clone` re-derives them with `git
    ls-tree` and is how that file was produced; `--verify-base-trees` checks one against the
    other, because a cache nobody re-derives is a cache nobody can trust.
    """
    if clone is None:
        cached = json.loads(BASE_TREES.read_text())[task]
        assert cached["pre_fix"] == commit, f"{task}: cached tree is not {commit}"
        return set(cached["paths"])
    out = subprocess.run(["git", "-C", str(clone), "ls-tree", "-r", "--name-only", commit],
                         capture_output=True, text=True, check=True)
    return set(out.stdout.split())


def _norm(raw: str) -> str | None:
    """A tool's path argument -> repo-relative, or None when it is not inside a repo.

    Returns the sentinel `""` for sinks that are not files at all (/dev/null, `&1`), which
    the callers drop rather than counting as an unresolved write.
    """
    p = raw.strip().strip("'\"")
    if not p or p.startswith("-"):
        return None
    if p.startswith("&") or p.startswith("/dev/"):
        return ""                         # fd dup or a null/tty sink: not a write to a file
    # Runs happen in a per-arm scratch checkout; everything below `.../repo/` is the tree.
    if "/repo/" in p:
        return p.split("/repo/", 1)[1]
    if p.startswith("/"):
        return None                       # absolute, outside the checkout
    return p.lstrip("./")


def _bash_targets(cmd: str) -> tuple[list[str], int, int]:
    """-> (repo-relative targets, writes landing outside the repo, unresolvable writes).

    `unresolved` is reserved for a write this parser genuinely cannot place. A redirect to
    /dev/null is not a write; a redirect to an absolute path outside the checkout is a write
    we CAN place, just not in the tree. Conflating either with "unknown" would make the
    unknown column meaningless, and an unknown that cries wolf is worse than no column.
    """
    cmd = NONWRITE_REDIRECT.sub(" ", cmd)
    if not WRITE_HINTS.search(cmd) and not (PY_WRITE.search(cmd) and PY_OPEN_W.search(cmd)):
        return [], 0, 0
    found, outside, unresolved = [], 0, 0
    saw_candidate = False
    for pat in TARGET_PATTERNS:
        for m in pat.finditer(cmd):
            n = _norm(m.group(1))
            if n == "":
                saw_candidate = True      # /dev/null and friends: a sink, not a write
                continue
            saw_candidate = True
            if n is None:
                outside += 1
            else:
                found.append(n)
    # A python -c that opens something for writing, target unknown to a regex.
    if PY_WRITE.search(cmd) and PY_OPEN_W.search(cmd):
        unresolved += 1
    if not saw_candidate and not unresolved:
        unresolved += 1                   # hint matched, nothing extractable at all
    return found, outside, unresolved


def load_calls(run: Path, arm: str, record: dict, source: str) -> tuple[list[dict], dict, dict]:
    """-> (calls in issue order, result envelope, trace-health counters).

    Two sources, and they must agree. `trace` re-parses the 1.7 MB-per-run `trace.jsonl`.
    `records` reads the `tool_calls` array the harness already saved inside `records.json`,
    which carries the same id/index/name/input/result/is_error per call plus the envelope --
    2.8 MB for all twelve rows against 64 MB of traces.

    The records path is what makes the closeout reproducible from artifacts small enough to
    live in the repository: the published tables rebuild from it with no model invoked and no
    external fixture directory. `--check-sources` runs both and diffs them.
    """
    if source == "records":
        return (list(record["tool_calls"]), record.get("result") or {},
                record.get("trace_health") or {})
    t = parse(run / arm / "trace.jsonl")
    return ([c for c in t["calls"] if c["name"] != "<orphan result>"],
            t["result"] or {},
            {"orphan_results": t["orphans"], "unresolved_calls": t["unresolved"]})


def classify(run: Path, arm: str, tracked: set[str], record: dict,
             source: str = "trace") -> dict:
    """One arm-run, described. Every count here is measured, never assumed."""
    calls, env, health = load_calls(run, arm, record, source)

    first_src_mutation = None      # issue-order index of the first one
    src_mutations, new_file_writes = 0, 0
    outside_writes, unknown_writes = 0, 0
    retrieval, notes_reads, tool_errors, test_runs = [], 0, 0, 0
    got_ids: list[str] = []

    for c in calls:
        name, inp, err = c["name"], c["input"] or {}, bool(c["is_error"])
        if err:
            tool_errors += 1

        if name.startswith("mcp__nexus__"):
            retrieval.append(c)
            if name == "mcp__nexus__get" and not err:
                # The id the model asked for; the body it got back is measured elsewhere
                # by delivered_context.py, which owns the bytes figure.
                mid = inp.get("memory_id") or inp.get("id")
                if mid:
                    got_ids.append(str(mid))

        targets, outside, unresolved = [], 0, 0
        if name in DIRECT_MUTATORS:
            n = _norm(str(inp.get("file_path") or inp.get("path") or ""))
            if n:
                targets = [n]
            elif n is None:
                outside += 1              # a direct mutator aimed outside the checkout
        elif name == "Bash":
            cmd = str(inp.get("command", ""))
            if TEST_RUN.search(cmd):
                test_runs += 1
            if NOTES_NAME in cmd:
                notes_reads += 1
            targets, outside, unresolved = _bash_targets(cmd)
        elif name == "Read" and NOTES_NAME in str(inp.get("file_path", "")):
            notes_reads += 1

        if err:
            continue                       # a failed call mutated nothing
        for n in targets:
            if n in tracked:
                src_mutations += 1
                if first_src_mutation is None:
                    first_src_mutation = c["index"]
            else:
                new_file_writes += 1
        outside_writes += outside
        unknown_writes += unresolved

    before = sum(1 for c in retrieval
                 if first_src_mutation is None or c["index"] < first_src_mutation)
    after = len(retrieval) - before
    usage = env.get("usage", {}) or {}
    return {
        "first_src_mutation": first_src_mutation,
        "src_mutations": src_mutations,
        "new_file_writes": new_file_writes,
        "outside_repo_writes": outside_writes,
        "unknown_writes": unknown_writes,
        "retrieval_calls": len(retrieval),
        "retrieval_before_mutation": before if first_src_mutation is not None else None,
        "retrieval_after_mutation": after if first_src_mutation is not None else None,
        "retrieval_no_mutation": len(retrieval) if first_src_mutation is None else None,
        "unique_bodies": len(set(got_ids)),
        "body_fetches": len(got_ids),
        "repeat_deliveries": len(got_ids) - len(set(got_ids)),
        "notes_reads": notes_reads,
        "tool_calls": len(calls),
        "num_turns": env.get("num_turns"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "tool_errors": tool_errors,
        "permission_denials": len(env.get("permission_denials") or []),
        "test_runs": test_runs,
        "terminal": env.get("terminal_reason"),
        "trace_health": health,
        "source": source,
    }


def collect(runroot: Path, clone: Path | None, source: str = "trace") -> list[dict]:
    tasks = json.loads(TASKS_FILE.read_text())["tasks"]
    rows = []
    for spec in tasks:
        task = spec["task"]
        tracked = tracked_paths(clone, task, spec["pre_fix"])
        for attempt in (1, 2, 3):
            run = runroot / f"run-{task}" / f"attempt{attempt}"
            rec = json.loads((run / "records.json").read_text())
            by_arm = {r["arm"]: r for r in rec["records"]}
            for arm in ARMS:
                r = by_arm[arm]
                row = {"task": task, "attempt": attempt, "arm": arm}
                row.update(classify(run, arm, tracked, r, source))
                row["patch_bytes"] = r["patch_bytes"]
                row["patch_touches_src"] = r["patch_touches_src"]
                row["passed"] = r["scored"]["passed"]
                row["scored_summary"] = r["scored"]["summary"]
                row["wall_clock_s"] = r["wall_clock_s"]
                rows.append(row)
    return rows


def _cell(v):
    return "unknown" if v is None else ("yes" if v is True else ("no" if v is False else str(v)))


def render(rows: list[dict]) -> str:
    head = ("| task | att | arm | 1st src mut | src muts | new files | unk writes | "
            "retr | before | after | uniq bodies | repeats | turns | calls | in tok | "
            "out tok | errs | denials | tests | patch B | src? | pass | terminal |")
    sep = "|" + "---|" * 23
    out = [head, sep]
    for r in rows:
        out.append("| " + " | ".join(_cell(x) for x in (
            r["task"], r["attempt"], r["arm"], r["first_src_mutation"], r["src_mutations"],
            r["new_file_writes"], r["unknown_writes"], r["retrieval_calls"],
            r["retrieval_before_mutation"], r["retrieval_after_mutation"],
            r["unique_bodies"], r["repeat_deliveries"], r["num_turns"], r["tool_calls"],
            r["input_tokens"], r["output_tokens"], r["tool_errors"],
            r["permission_denials"], r["test_runs"], r["patch_bytes"],
            r["patch_touches_src"], r["passed"], r["terminal"])) + " |")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    runroot = Path(argv[1])
    # The clone is optional: without it the committed base trees are used, which is the
    # self-contained path a later reader takes.
    clone = Path(argv[2]) if len(argv) > 2 and not argv[2].startswith("--") else None

    if "--verify-base-trees" in argv:
        if clone is None:
            print("--verify-base-trees needs the clone to compare against", file=sys.stderr)
            return 2
        bad = []
        for spec in json.loads(TASKS_FILE.read_text())["tasks"]:
            live = tracked_paths(clone, spec["task"], spec["pre_fix"])
            cached = tracked_paths(None, spec["task"], spec["pre_fix"])
            if live != cached:
                bad.append((spec["task"], len(live ^ cached)))
        for t, n in bad:
            print(f"  MISMATCH {t}: {n} paths differ")
        print(f"base trees: {'identical' if not bad else str(len(bad)) + ' tasks differ'}")
        return 1 if bad else 0
    source = "records" if "--from-records" in argv else "trace"

    if "--check-sources" in argv:
        # The reproducibility claim, tested rather than asserted: every measured field must
        # come out the same whether it was read from the 64 MB of traces or the 2.8 MB of
        # records. Fields that name their own source are excluded from the comparison.
        a = collect(runroot, clone, "trace")
        b = collect(runroot, clone, "records")
        skip = {"source", "trace_health"}
        diffs = [(x["task"], x["attempt"], x["arm"], k, x[k], y[k])
                 for x, y in zip(a, b) for k in x if k not in skip and x[k] != y[k]]
        for d in diffs:
            print(f"  MISMATCH {d[0]}/a{d[1]}/{d[2]} {d[3]}: trace={d[4]!r} records={d[5]!r}")
        print(f"{len(a)} arm-runs compared across both sources: "
              f"{'identical' if not diffs else str(len(diffs)) + ' mismatches'}")
        return 1 if diffs else 0

    rows = collect(runroot, clone, source)
    table = render(rows)
    print(table)

    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(rows, indent=1) + "\n")
    if "--md" in argv:
        Path(argv[argv.index("--md") + 1]).write_text(table + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
