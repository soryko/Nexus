"""A per-memory provenance review of `dev-m1`, covering every memory an arm can receive.

The identifier scan returns `unresolved` on this task set and cannot be made to return
anything else: none of k1-k4's fixes introduces an identifier absent from its pre-fix tree,
so there is no distinctive token to scan for. A scan that cannot fire settles nothing, and a
memory that conveys a fix IN PROSE would pass every scan there is.

So what fix leakage rests on here is reading, and this file is the reading: for each of the
24 memories, the source it came from, what its author had seen, what its probe actually
supports as against what its prose asserts, which register it is written in, and whether it
is suitable for this comparison.

The result is **reviewed provenance with limited automated leakage checks**. It is not proof
that no memory conveys a fix. It is weaker than a measurement and is labelled as weaker.

    <venv>/bin/python review_provenance.py [--out PROVENANCE-dev-m1.md] [--json <path>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import verify_dev_m1 as V                                                  # noqa: E402

#: The task revisions, from `tasks-calib-a2.json`. Used for the fix-locality scan below.
REVISIONS = {"k1": ("a8c0542760", "c326df95e9"), "k2": ("011b9f9d19", "884af5c20f"),
             "k3": ("2ed395b0b5", "27aaed3fe5"), "k4": ("273fb90106", "70c673d37e")}

# ---------------------------------------------------------------------------------------
# DECLARED JUDGEMENTS. Reviewed by reading each memory against its task's real fix diff.
# They are judgements and are marked as judgements; the computed columns sit beside them.
#
# exposure  what the memory's AUTHOR had seen when it was written
# register  describes_existing | diagnostic_guidance | conveys_repair
# ---------------------------------------------------------------------------------------

CAPTURED = ("captured during A1's corpus capture, by a session that had not read A1's "
            "held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a "
            "weaker claim than 'written blind to k1-k4' and is stated as the weaker one. "
            "Written against a Click revision NEWER than k1/k2/k4's checkouts.")
DERIVED = ("written during dev-m1's construction by an author who HAD seen k1-k4's subjects "
           "and their pre-fix trees. Whether that author also read the fix diffs is not "
           "established by any artifact, and is not assumed either way.")
DERIVED_ENV = ("written from `prompts-calib-a2.json`'s `environment` block and "
               "REPAIR-interpreter-consistency.md. The author had seen the task ENVIRONMENT; "
               "no task's fix or checks bear on it.")
DISTRACTOR = ("written during dev-m1's construction by an author who had seen k1-k4, and "
              "chosen deliberately to bear on none of them.")

EXISTING = "describes existing behaviour"
GUIDANCE = "diagnostic guidance"
REPAIR = "conveys the repair"

REVIEW: dict[str, dict] = {
    # --- captured, on-subject ---------------------------------------------------------
    "h01": {"exposure": CAPTURED, "register": EXISTING, "suitable": True,
            "note": "Two notions of 'used without a value'. k2's fix hoists a `flag_value` "
                    "assignment out of a `type is None` branch; nothing here states that."},
    "h02": {"exposure": CAPTURED, "register": EXISTING, "suitable": True,
            "note": "Auto-detection in `Option.__init__`. REFUTED at k2's checkout -- a "
                    "version mismatch, not an observed rotting."},
    "h05": {"exposure": CAPTURED, "register": EXISTING, "suitable": True,
            "note": "`default` and `flag_value` answer different questions. The k2 fix makes "
                    "`flag_value` derive from `default` in one more case; this says the "
                    "opposite of a rule, not the change."},
    "h07": {"exposure": CAPTURED, "register": EXISTING, "suitable": True,
            "note": "Parser token consumption. Neither k2's nor k3's fix touches the parser."},
    "h08": {"exposure": CAPTURED, "register": EXISTING, "suitable": True,
            "note": "Callable defaults. k3's fix is about WHEN `UNSET` becomes `None`, not "
                    "about calling factories."},
    "h09": {"exposure": CAPTURED, "register": EXISTING, "suitable": True,
            "note": "The issue-3024 reconciliation. k3's fix cites issues 3071/3079 and is a "
                    "different mechanism; naming an issue number is not naming this one."},
    "h11": {"exposure": CAPTURED, "register": EXISTING, "suitable": True,
            "note": "The `consume_value` precedence chain. k3's fix changes the same method, "
                    "so this is the captured memory nearest a fix site -- but it states the "
                    "precedence ORDER, and the fix changes the UNSET normalisation POINT. "
                    "Nearest, and still not the repair."},
    "h13": {"exposure": CAPTURED, "register": EXISTING, "suitable": True,
            "note": "`flag_value` as a value throughout, including type inference when no "
                    "explicit type is given -- the PRE-fix coupling k2's fix breaks. It "
                    "describes the state the fix changes, which is the opposite of conveying "
                    "it."},
    # --- captured, tooling / no subject ------------------------------------------------
    "h03": {"exposure": CAPTURED, "register": GUIDANCE, "suitable": True,
            "note": "`uv run` fails in the sandbox. Environment, not mechanism."},
    "h04": {"exposure": CAPTURED, "register": GUIDANCE, "suitable": True,
            "note": "Parametrised option tests through CliRunner. Procedure."},
    "h06": {"exposure": CAPTURED, "register": GUIDANCE, "suitable": True,
            "note": "`filterwarnings = error` aborts collection. Environment."},
    "h10": {"exposure": CAPTURED, "register": GUIDANCE, "suitable": True,
            "note": "Running the suite outside `uv`. Environment."},
    "h12": {"exposure": CAPTURED, "register": GUIDANCE, "suitable": True,
            "note": "CliRunner as the exercise path. Procedure."},
    # --- derived ------------------------------------------------------------------------
    "m01": {"exposure": DERIVED, "register": EXISTING, "suitable": True,
            "note": "Where cleanup callbacks are held and who runs them, ending on 'closing "
                    "it is the caller's responsibility'. k1's fix makes `Context.exit()` call "
                    "`self.close()`. This names the mechanism and the site WITHOUT naming "
                    "`exit()` or stating that it should close -- the reader still has to find "
                    "the path that bypasses `close()`. Closest of the k1 memories; not the "
                    "repair."},
    "m02": {"exposure": DERIVED, "register": GUIDANCE, "suitable": True, "flagged": True,
            "note": "THE MATERIAL FINDING OF THIS REVIEW. It states both halves of k4's "
                    "defect -- that `get_help_option` CONSTRUCTS an option each call, and "
                    "that `iter_params_for_processing` compares the parameter OBJECTS -- and "
                    "the fix's own in-code rationale reads 'avoid creating it multiple times. "
                    "Not doing this will break the callback odering by "
                    "iter_params_for_processing(), which relies on object comparison'. Same "
                    "two clauses, minus the remedy (caching in `self._help_option`). Written "
                    "by an author who had seen k4. No identifier scan could reach this: every "
                    "name in it predates the fix. It is diagnosis, not repair, so it stays in "
                    "the corpus -- but a k4 result under either policy may NOT be read as "
                    "evidence that retrieval located the mechanism unaided."},
    "m03": {"exposure": DERIVED, "register": EXISTING, "suitable": True,
            "note": "`_flag_needs_value = flag_value is not None`. True at k1/k2/k4, REFUTED "
                    "at k3 -- the older form presented to the newer checkout."},
    "m04": {"exposure": DERIVED_ENV, "register": GUIDANCE, "suitable": True,
            "note": "`PYTHONPATH=src` and a named interpreter. Environment."},
    "m05": {"exposure": DERIVED, "register": EXISTING, "suitable": True,
            "note": "CONSTRUCTED CONTRADICTION for k1. Asserts no revision of this "
                    "repository; refuted one read away at `Context.__init__`. Tests whether "
                    "an agent notices a memory the checkout refutes."},
    "m06": {"exposure": DERIVED, "register": EXISTING, "suitable": True,
            "note": "CONSTRUCTED CONTRADICTION for k4. Asserts ordering is preserved and "
                    "stable; k4's fix documents the opposite. Refuted at "
                    "`Command.get_help_option_names`."},
    # --- distractors ---------------------------------------------------------------------
    "x01": {"exposure": DISTRACTOR, "register": EXISTING, "suitable": True,
            "note": "termui styling. No task's fix touches `src/click/termui.py`."},
    "x02": {"exposure": DISTRACTOR, "register": EXISTING, "suitable": True,
            "note": "shell completion. No task's fix touches it."},
    "x03": {"exposure": DISTRACTOR, "register": EXISTING, "suitable": True,
            "note": "`UsageError`. No task's fix touches `src/click/exceptions.py`."},
    "x04": {"exposure": DISTRACTOR, "register": EXISTING, "suitable": True,
            "note": "the `Path` type. No task's fix touches `src/click/types.py`."},
    "x05": {"exposure": DISTRACTOR, "register": EXISTING, "suitable": True,
            "note": "`Group` dispatch. In `core.py`, which every fix touches, but on "
                    "subcommand resolution, which none of them does."},
}

#: A token the memory presents AS CODE: inside backticks, or shaped like an identifier
#: (underscore or CamelCase) rather than like an English word.
#:
#: Without this the scan matched `value`, `option`, `order`, `close` and `which` -- k4's fix
#: adds a prose docstring, so its "changed lines" are largely English. Two earlier versions
#: of the sibling scan in `verify_dev_m1` failed in exactly this direction and flagged 5-20
#: of 24 memories per task. A check that fires on everything is as uninformative as one that
#: cannot fire at all.
BACKTICKED = re.compile(r"`([^`]+)`")
IDENTIFIER_SHAPED = re.compile(r"^(?:[a-z]+_[a-z_0-9]+|_[A-Za-z0-9_]+|[A-Z][a-z]+[A-Z]\w*)$")


def code_tokens_of(content: str) -> set[str]:
    """Identifiers the memory itself presents as code."""
    out = set()
    for span in BACKTICKED.findall(content):
        out |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", span))
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", content):
        if IDENTIFIER_SHAPED.match(w):
            out.add(w)
    return out


def fix_touched_identifiers(task: str, clone: Path) -> set[str]:
    """Identifiers on ANY changed line of the task's fix -- added or removed.

    Different from `verify_dev_m1.fix_added_tokens`, which asks for identifiers the fix
    INTRODUCES and finds none on this task set, so it can never fire. This one asks what
    the fix TOUCHES, which is weaker evidence and at least measurable.
    """
    pre, fix = REVISIONS[task]
    d = subprocess.run(["git", "-C", str(clone), "diff", f"{pre}..{fix}", "--", "src/"],
                       capture_output=True, text=True)
    if d.returncode != 0:
        raise V.Unresolved(f"{task}: git diff {pre}..{fix} failed in {clone}")
    lines = [ln[1:] for ln in d.stdout.splitlines()
             if (ln.startswith("+") and not ln.startswith("+++"))
             or (ln.startswith("-") and not ln.startswith("---"))]
    return V._code_tokens("\n".join(lines))


def prose_claims(content: str) -> int:
    """Sentences in the memory. A crude count, and the point it makes is not crude: a probe
    checking three regular expressions does not establish a paragraph of prose."""
    return len([s for s in re.split(r"(?<=[.;])\s+", content.strip()) if len(s) > 15])


def build(clone: Path | None = None) -> dict:
    corpus = json.loads((BENCH / "corpus-dev-m1.json").read_text())
    results = json.loads((BENCH / "results-dev-m1-r2.json").read_text())
    truth = results["truth_at_checkout"]
    clone = Path(clone or V._config_clone() or "")
    touched, locality_state = {}, "measured"
    try:
        if not clone.is_dir():
            raise V.Unresolved(f"clone {clone} is not a directory")
        touched = {t: fix_touched_identifiers(t, clone) for t in REVISIONS}
    except V.Unresolved as exc:
        locality_state = f"unresolved: {exc}"

    rows = []
    for m in corpus["memories"]:
        mid = m["id"]
        judged = REVIEW.get(mid)
        if judged is None:
            raise SystemExit(f"{mid} has no provenance review; every memory an arm can "
                             f"receive must be reviewed, not only the on-topic ones")
        names = code_tokens_of(m["content"])
        locality = {t: sorted(touched.get(t, set()) & names)
                    for t in REVISIONS} if touched else {}
        probe = m.get("probe") or {}
        rows.append({
            "id": mid, "provenance": m["provenance"], "kind": m["kind"],
            "subject": V.SUBJECT.get(mid, []),
            "source": (f"captured memory {m['captured_memory_id']}"
                       if m.get("captured_memory_id") else m.get("derived_from")
                       or "written for dev-m1; no source revision asserted"),
            "exposure": judged["exposure"], "register": judged["register"],
            "suitable": judged["suitable"], "flagged": judged.get("flagged", False),
            "note": judged["note"],
            "probe_file": probe.get("file"),
            "probe_patterns": list(probe.get("all", ())) + [f"NOT {p}" for p in
                                                            probe.get("none", ())],
            "probe_supports_n": len(probe.get("all", ())) + len(probe.get("none", ())),
            "prose_claims_n": prose_claims(m["content"]),
            "truth": {t: truth[mid][t] for t in REVISIONS},
            "fix_locality": locality,
        })
    return {
        "corpus_version": corpus["corpus_version"],
        "corpus_sha256": hashlib.sha256(
            (BENCH / "corpus-dev-m1.json").read_bytes()).hexdigest(),
        "memories_reviewed": len(rows),
        "memories_in_corpus": len(corpus["memories"]),
        "fix_locality_state": locality_state,
        "fix_locality_rule": "identifiers the memory presents AS CODE (backticked, or "
                             "underscore/CamelCase shaped) that also appear on a changed "
                             "line of that task's fix. Weaker than the added-identifier "
                             "scan and, unlike it, able to fire.",
        "conclusion": "reviewed provenance with limited automated leakage checks",
        "headline": (
            "The one memory this review flags -- `m02`, which states both halves of k4's "
            "defect in the same two clauses as the fix's own rationale comment -- is a "
            "memory the fix-locality scan shows NOTHING for, on k4 or on any task. Every "
            "identifier in it predates the fix, and the four words it shares with k4's "
            "changed lines (`invocation`, `option`, `order`, `parameters`) are English "
            "prose from an added docstring, which is why they are excluded. Meanwhile the "
            "scan's own hits are `close`, `flag_value`, `is_flag`, `default`, `value`, "
            "`UNSET` and `click` -- the working vocabulary of every memory about option "
            "handling, on-subject and off. So on this task set the scan flags memories the "
            "reading clears and clears the memory the reading flags. That is the concrete "
            "form of the general claim that identifier scans cannot establish semantic "
            "leakage, and it is why the conclusion rests on reading."),
        "not_established": [
            "that no memory conveys a fix semantically -- reading is not measurement",
            "that the derived memories' author did not read the fix diffs; no artifact "
            "records either way",
            "that a `true` probe certifies every sentence of the prose it labels",
        ],
        "rows": rows,
    }


def render(rep: dict) -> str:
    L = [f"# `dev-m1` provenance review — all {rep['memories_reviewed']} memories",
         "",
         f"**{rep['conclusion'].capitalize()}.** Not proof that no memory conveys a fix.",
         "",
         f"| corpus | `corpus-dev-m1.json`, sha256 `{rep['corpus_sha256']}` |",
         "| --- | --- |",
         f"| reviewed | {rep['memories_reviewed']} of {rep['memories_in_corpus']} — "
         f"**every memory an arm can receive**, not only the on-subject ones |",
         f"| fix-locality scan | {rep['fix_locality_state']} |", "",
         "## The finding", "", rep["headline"], "",
         "## Per memory", "",
         "| id | prov | subject | register | probe / prose | k1 k2 k3 k4 | fix-locality |",
         "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rep["rows"]:
        loc = {t: v for t, v in (r["fix_locality"] or {}).items() if v}
        locs = ", ".join(f"{t}:{'+'.join(v)}" for t, v in loc.items()) or "—"
        flag = " ⚑" if r["flagged"] else ""
        L.append(f"| `{r['id']}`{flag} | {r['provenance'][:4]} | "
                 f"{','.join(r['subject']) or '—'} | {r['register']} | "
                 f"{r['probe_supports_n']} / {r['prose_claims_n']} | "
                 f"{' '.join(r['truth'][t][:1] for t in ('k1','k2','k3','k4'))} | {locs} |")
    L += ["", "## Notes, per memory", ""]
    for r in rep["rows"]:
        L.append(f"**`{r['id']}`**{' ⚑ FLAGGED' if r['flagged'] else ''} — "
                 f"*source:* {r['source']}  ")
        L.append(f"*author had seen:* {r['exposure']}  ")
        if r["probe_patterns"]:
            L.append(f"*probe supports:* `{r['probe_file']}` matching "
                     + ", ".join(f"`{p}`" for p in r["probe_patterns"])
                     + f" — {r['probe_supports_n']} structural observation(s) against "
                       f"{r['prose_claims_n']} prose claim(s).  ")
        else:
            L.append("*probe supports:* nothing — this memory has no probe, so its truth is "
                     "`unknown` at every checkout and it is support, never on-subject.  ")
        L.append(f"*register:* {r['register']}. {r['note']}")
        L.append("")
    L += ["## What this review does not establish", ""]
    L += [f"- {n}" for n in rep["not_established"]]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clone")
    ap.add_argument("--out", default=str(BENCH / "PROVENANCE-dev-m1.md"))
    ap.add_argument("--json", default=str(BENCH / "provenance-dev-m1.json"))
    a = ap.parse_args()
    rep = build(a.clone)
    Path(a.out).write_text(render(rep) + "\n")
    Path(a.json).write_text(json.dumps(rep, indent=1, default=str) + "\n")
    flagged = [r["id"] for r in rep["rows"] if r["flagged"]]
    print(f"{rep['memories_reviewed']} memories reviewed -> {a.out}")
    print(f"fix-locality: {rep['fix_locality_state']}")
    print(f"flagged: {', '.join(flagged) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
