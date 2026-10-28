"""Build `corpus-dev-m1.json`: a memory corpus MATCHED to k1-k4, with a probe per memory.

A1's held-out corpus was deliberately UNMATCHED to these tasks. That made it right for
measuring retrieval overhead during calibration and useless for asking whether a cheaper
consultation policy still reaches information that matters -- there was nothing that mattered
to reach. This corpus is matched, and every memory carries a PROBE: a mechanical check
against a task's own checkout that says whether the memory's claim holds there. The labels
are then computed from the probes rather than asserted, which is the only reason they can be
frozen before any model outcome.

k1-k4 are EXPOSED tasks. They have been run. This is a DEVELOPMENT set and nothing here is,
or may later be relabelled as, held-out evidence.

Three provenance classes, never merged:

  captured   the thirteen memories of the frozen `heldout-a1` corpus, written by a prior
             session that had not read any task. Carried verbatim -- content, kind and tags
             -- with their original ids.
  derived    written here, but every sentence is a statement about code in a real checkout of
             this repository, with the file and the revision recorded. A SYNTHETIC MECHANISM
             TEST: the wording is constructed, the fact is not.
  distractor written here, true of the repository, and irrelevant to all four tasks. These
             exist to make "useful information among unrelated memories" a measurable
             condition rather than an adjective.

    python3 build_dev_m1.py [--out corpus-dev-m1.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BENCH = Path(__file__).parent
CAPTURED = BENCH / "corpus-heldout-a1.json"

#: Probe grammar. `file` is relative to a task checkout; `all`/`none` are regular expressions
#: that must all match / must not match. A probe that cannot be evaluated is `unknown`, never
#: `false`: a missing file is not a refuted claim.
DERIVED = [
    {
        "id": "m01",
        "kind": "constraint",
        "tags": ["context", "cleanup", "core"],
        "content": (
            "Cleanup callbacks registered with `ctx.call_on_close()` are collected in "
            "`Context._close_callbacks` and run by `Context.close()`, which `Context.__exit__` "
            "calls. `Command.make_context` parses arguments inside `with ctx.scope("
            "cleanup=False):` and then RETURNS the context to its caller, so closing it is the "
            "caller's responsibility and does not happen inside `make_context`."),
        "derived_from": "src/click/core.py at each task's own checkout",
        "probe": {"file": "src/click/core.py",
                  "all": [r"_close_callbacks", r"def close\(self\)",
                          r"with ctx\.scope\(cleanup=False\)"]},
    },
    {
        "id": "m02",
        "kind": "decision",
        "tags": ["core", "help", "params"],
        "content": (
            "`Command.get_help_option(ctx)` CONSTRUCTS an option from `ctx.help_option_names` "
            "each time it is called, and `Command.get_params(ctx)` appends whatever it returns "
            "to the parameter list it hands back. `iter_params_for_processing` orders "
            "parameters by whether they appear in the invocation order and by `is_eager`, "
            "comparing the parameter OBJECTS it is given."),
        "derived_from": "src/click/core.py at each task's own checkout",
        "probe": {"file": "src/click/core.py",
                  "all": [r"def get_help_option", r"def get_params",
                          r"def iter_params_for_processing"]},
    },
    {
        "id": "m03",
        "kind": "constraint",
        "tags": ["flags", "options", "parsing"],
        "content": (
            "An option's `_flag_needs_value` is set from whether a `flag_value` was supplied: "
            "`self._flag_needs_value = flag_value is not None`. There is no separate sentinel "
            "for a flag supplied without a value; the parser stores `None` and "
            "`consume_value` resolves it."),
        "derived_from": "src/click/core.py at the k1/k2/k4 revisions; REFUTED at k3's",
        "probe": {"file": "src/click/core.py",
                  "all": [r"_flag_needs_value = flag_value is not None"]},
    },
    {
        "id": "m04",
        "kind": "procedure",
        "tags": ["pytest", "testing", "tooling"],
        "content": (
            "The checkout is a `src/` layout and is not installed. Run anything against it "
            "with `PYTHONPATH=src`, and the test suite with "
            "`PYTHONPATH=src <interpreter> -m pytest <paths> -q`. A bare `python3` on this "
            "machine may resolve to an interpreter with no pytest."),
        "derived_from": "prompts-calib-a2.json `environment`, and REPAIR-interpreter-consistency.md",
        "probe": {"file": "pyproject.toml", "all": [r"\[project\]"]},
    },
    {
        "id": "m05",
        "kind": "decision",
        "tags": ["context", "cleanup", "core"],
        "content": (
            "Callbacks registered with `ctx.call_on_close()` go onto the context's "
            "`ExitStack` -- `self._exit_stack.callback(f)` -- and `Context.close()` is just "
            "`self._exit_stack.close()`. There is no separate `_close_callbacks` list; the "
            "stack is the only place a cleanup is held."),
        "derived_from": (
            "CONSTRUCTED CONTRADICTION. No revision of this repository is asserted. It is "
            "refuted by `Context.__init__`, which holds `_close_callbacks` and `_exit_stack` "
            "as separate attributes, and by `Context.close()`, which walks the list."),
        "contradiction_discoverable_at": "src/click/core.py, Context.__init__ and Context.close",
        "probe": {"file": "src/click/core.py", "none": [r"_close_callbacks"]},
    },
    {
        "id": "m06",
        "kind": "decision",
        "tags": ["core", "help", "params"],
        "content": (
            "`Command.get_help_option_names(ctx)` preserves the order given in "
            "`ctx.help_option_names`, so the first name declared there is the first returned "
            "and the option's primary spelling is stable."),
        "derived_from": (
            "CONSTRUCTED CONTRADICTION. No revision of this repository is asserted. It is "
            "refuted by the method's own body, which builds `set(ctx.help_option_names)` and "
            "returns `list(all_names)` -- an unordered set, so declaration order is not "
            "preserved."),
        "contradiction_discoverable_at":
            "src/click/core.py, Command.get_help_option_names",
        "probe": {"file": "src/click/core.py",
                  "none": [r"all_names = set\(ctx\.help_option_names\)"]},
    },
]

DISTRACTORS = [
    {
        "id": "x01",
        "kind": "constraint",
        "tags": ["termui", "output"],
        "content": (
            "`click.style()` composes ANSI escape sequences and `click.secho()` is `echo` with "
            "`style` applied to its message; `click.unstyle()` strips them again. Colour is "
            "stripped automatically when the output stream is not a terminal."),
        "probe": {"file": "src/click/termui.py", "all": [r"def style", r"def secho"]},
    },
    {
        "id": "x02",
        "kind": "constraint",
        "tags": ["completion", "shell"],
        "content": (
            "Shell completion lives in `src/click/shell_completion.py`: a `CompletionItem` "
            "carries the value and its help text, and `ParamType.shell_complete` is the hook a "
            "custom type implements to offer completions."),
        "probe": {"file": "src/click/shell_completion.py",
                  "all": [r"class CompletionItem", r"shell_complete"]},
    },
    {
        "id": "x03",
        "kind": "decision",
        "tags": ["exceptions", "usage"],
        "content": (
            "`UsageError` carries an optional context and prints the command's usage line "
            "before its message; `ParamType.fail()` raises the parameter-scoped variant so the "
            "offending parameter is named in the output."),
        "probe": {"file": "src/click/exceptions.py", "all": [r"class UsageError"]},
    },
    {
        "id": "x04",
        "kind": "procedure",
        "tags": ["paths", "types"],
        "content": (
            "The `click.Path` type checks existence, file/directory kind and readability "
            "before conversion, and `resolve_path=True` makes it return an absolute, symlink-"
            "resolved path rather than the string the user typed."),
        "probe": {"file": "src/click/types.py", "all": [r"class Path", r"resolve_path"]},
    },
    {
        "id": "x05",
        "kind": "constraint",
        "tags": ["groups", "invocation"],
        "content": (
            "A `Group` resolves a subcommand name through `Group.get_command(ctx, name)` and "
            "lists what it can offer through `list_commands(ctx)`; `invoke_without_command` "
            "decides whether the group's own callback runs when no subcommand is given."),
        "probe": {"file": "src/click/core.py",
                  "all": [r"def get_command", r"def list_commands",
                          r"invoke_without_command"]},
    },
]


def build() -> dict:
    captured = json.loads(CAPTURED.read_text())
    memories = []
    for m in captured["memories"]:
        memories.append({
            "id": m["id"], "kind": m["kind"], "tags": m["tags"], "content": m["content"],
            "provenance": "captured",
            "captured_memory_id": m["memory_id"],
            # A captured memory's claim is checked the same way, by a probe written HERE. The
            # probe is this corpus's instrument; the memory is the prior session's evidence.
            "probe": CAPTURED_PROBES.get(m["id"]),
        })
    for m in DERIVED:
        memories.append({**{k: v for k, v in m.items()}, "provenance": "derived",
                         "synthetic_mechanism_test": True})
    for m in DISTRACTORS:
        memories.append({**{k: v for k, v in m.items()}, "provenance": "distractor",
                         "synthetic_mechanism_test": True})
    return {
        "corpus_version": "dev-m1",
        "status": "draft",
        "policy": "capture-policy-a1.md for the captured half; this file for the rest",
        "registration": "FREEZE-dev-m1.md",
        "repository": "pallets/click",
        "task_set": ["k1", "k2", "k3", "k4"],
        "exposure": (
            "DEVELOPMENT ONLY. k1-k4 have been run (A2 calibration v2, and A2-R). They are "
            "exposed and may never be relabelled as held-out evidence. The captured half of "
            "this corpus was written blind to A1's held-out tasks; it was NOT written blind "
            "to k1-k4, because k1-k4 did not exist when it was captured -- which is a weaker "
            "claim and is stated as the weaker one."),
        "answer_key_note": (
            "`probe`, `provenance` and `synthetic_mechanism_test` are the evaluator's "
            "instrument and are withheld from both memory arms, exactly as `relevance` and "
            "`outdated_note` are withheld in A1. `seed_store.py` and `render_notes.py` "
            "deliver content, kind and tags and nothing else."),
        "memories": memories,
    }


#: One probe per captured memory. Written here rather than in the frozen corpus, which is not
#: edited. `None` means the claim is prose about intent that no regular expression settles --
#: recorded as `unknown`, never silently as true.
CAPTURED_PROBES = {
    "h01": {"file": "src/click/_utils.py", "all": [r"FLAG_NEEDS_VALUE"]},
    "h02": {"file": "src/click/core.py", "all": [r"_flag_needs_value = self\.default is UNSET"]},
    "h03": None,
    "h04": None,
    "h05": {"file": "src/click/core.py", "all": [r"flag_value", r"default"]},
    "h06": {"file": "pyproject.toml", "all": [r"filterwarnings"]},
    "h07": {"file": "src/click/parser.py", "all": [r"_get_value_from_state"]},
    "h08": {"file": "src/click/core.py", "all": [r"def get_default", r"call: bool"]},
    "h09": {"file": "src/click/core.py", "all": [r"3024"]},
    "h10": None,
    "h11": {"file": "src/click/core.py", "all": [r"def consume_value", r"default_map"]},
    "h12": None,
    "h13": {"file": "src/click/core.py", "all": [r"convert_type\(None, flag_value\)"]},
}


def main(argv: list[str]) -> int:
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else BENCH / "corpus-dev-m1.json"
    corpus = build()
    out.write_text(json.dumps(corpus, indent=1) + "\n")
    n = {p: sum(1 for m in corpus["memories"] if m["provenance"] == p)
         for p in ("captured", "derived", "distractor")}
    probed = sum(1 for m in corpus["memories"] if m.get("probe"))
    print(f"{len(corpus['memories'])} memories -> {out}")
    print(f"  captured {n['captured']}   derived {n['derived']}   distractor {n['distractor']}")
    print(f"  {probed} carry a probe; {len(corpus['memories']) - probed} are prose no regular "
          f"expression settles and will read `unknown`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
