"""Measure `corpus-dev-m1`'s labels, then freeze them. Model-free.

Four things are checked, and the labels are DERIVED from the first, not asserted:

  1. truth at the checkout   every probe, run against each of k1-k4's own pre-fix tree.
                             true / false / unknown. A missing file is `unknown`.
  2. no fix leakage          no memory may contain a distinctive token that appears only on
                             the ADDED side of that task's own fix diff. The corpus must not
                             carry the answer.
  3. no check leakage        no memory may name a function defined in that task's hidden
                             acceptance checks and nowhere in the visible tree.
  4. reachability            the useful memories must actually come back through the arm's
                             normal retrieval path, at a recorded RANK -- which is the
                             quantity a bounded consultation policy acts on. A corpus whose
                             useful memory ranks 14th cannot show benefit or harm; it shows
                             the retriever.

The category rule, stated before the numbers so it can be argued with:

  useful       at least one memory whose claim is TRUE at this checkout and whose subject is
               this task's mechanism.
  unnecessary  no memory's subject is this task's mechanism. Generic tooling and testing
               procedure do not make a task useful; they are `support` and set no category.
  stale        at least one memory whose claim is FALSE at this checkout AND whose subject is
               this task's mechanism -- so following it leads somewhere wrong.
  distracting  a property of the CORPUS, not of a task: it holds when the useful memories are
               a minority of what a single retrieval returns.

A task may carry more than one. `useful` and `stale` together is the interesting case and is
not resolved into one label.

    python3 verify_dev_m1.py <scratch> [--corpus <f>] [--out <f>] [--clone <d>] [--no-reach]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).parent
TASKS = ("k1", "k2", "k3", "k4")

#: Which task each memory's SUBJECT bears on, declared here and frozen. This is the one
#: judgement in the file and it is separated from the measurement on purpose: the probe says
#: whether a claim is true at a checkout, this says what the claim is about.
SUBJECT = {
    "h01": ["k2", "k3"], "h02": ["k2", "k3"], "h05": ["k2", "k3"], "h07": ["k2", "k3"],
    "h09": ["k3"], "h11": ["k3"], "h13": ["k2"], "h08": ["k3"],
    "h03": [], "h04": [], "h06": [], "h10": [], "h12": [],          # generic tooling
    "m01": ["k1"], "m02": ["k4"], "m03": ["k2", "k3"], "m04": [],
    "m05": ["k1"], "m06": ["k4"],
    "x01": [], "x02": [], "x03": [], "x04": [], "x05": [],
}

#: The query each arm's own retrieval makes first. `memory_probe_query` in the configuration
#: is the harness's reachability probe; these are the task-shaped queries a reader would
#: expect an agent to issue, and are frozen so the rank below is reproducible.
QUERIES = {
    "k1": "call_on_close cleanup callback eager option ctx.exit context close",
    "k2": "is_flag option explicit type flag_value wrong value",
    "k3": "two flag options same parameter name default selection",
    "k4": "help option generated help_option_names eager ordering",
}


def probe_tree(probe: dict | None, root: Path) -> str:
    if not probe:
        return "unknown"
    f = root / probe["file"]
    if not f.is_file():
        return "unknown"
    text = f.read_text(errors="replace")
    if not all(re.search(p, text) for p in probe.get("all", ())):
        return "false"
    if any(re.search(p, text) for p in probe.get("none", ())):
        return "false"
    return "true"


class Unresolved(Exception):
    """Evidence could not be gathered. The distinction this whole module turns on: a scan
    that gathered nothing found no leakage only if it actually looked."""


def _config_clone() -> str | None:
    """`a1-config.json` names the upstream clone. It is gitignored, so nothing in git records
    the path and it must be read at runtime or supplied."""
    cfg = BENCH / "a1-config.json"
    if not cfg.is_file():
        return None
    try:
        return json.loads(cfg.read_text()).get("source_clone")
    except (ValueError, OSError):
        return None


def _code_tokens(block: str) -> set[str]:
    """Identifiers in a block of Python, with comments and string bodies removed.

    The scan is looking for an identifier the fix introduces. Prose is not that. k4's fix
    adds a docstring reading "The invocation order takes precedence over the declaration
    order", and without this the scan flagged two memories for using the English words
    `precedence` and `declared` about unrelated subjects. Stripping comments and string
    bodies is what makes a hit mean "this memory names something the fix introduced".
    """
    block = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', " ", block)
    # A diff's added lines are not valid Python: a docstring can OPEN on a `+` line and close
    # on an unchanged one, leaving the region unterminated and unstripped. That is exactly
    # k4's fix, and it is why `precedence` and `takes` survived the pass above. An unmatched
    # opener therefore strips to the end of the block.
    block = re.sub(r'"""[\s\S]*|\'\'\'[\s\S]*', " ", block)
    block = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', " ", block)
    block = re.sub(r"#[^\n]*", " ", block)
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", block))


def fix_added_tokens(scratch: Path, task: str, clone: str | None = None) -> set[str]:
    """Distinctive identifiers appearing only on the ADDED side of the task's own fix.

    RAISES rather than returning an empty set when the diff could not be taken. The previous
    version returned `set()` for a missing clone, a missing revision or a failed `git`, and
    every one of those published as "no leakage" -- which is how this check ran vacuously
    across all four tasks: no A2-R `fixtures.json` records a `clone` key at all.
    """
    base = scratch / f"c45/run-{task}/base"
    fixtures = base / "fixtures.json"
    if not fixtures.is_file():
        raise Unresolved(f"{fixtures} is absent")
    spec = json.loads(fixtures.read_text())
    entry = spec[0] if isinstance(spec, list) else spec
    fix, pre = entry.get("fix"), entry.get("pre_fix")
    if not (fix and pre):
        raise Unresolved(f"{task}: fixtures.json records no fix/pre_fix pair")
    src = clone or entry.get("clone") or entry.get("source_clone") or _config_clone()
    if not src:
        raise Unresolved(f"{task}: no clone recorded in fixtures.json, and none supplied "
                         f"(--clone) or named by a1-config.json")
    if not Path(src).is_dir():
        raise Unresolved(f"{task}: clone {src} is not a directory")
    d = subprocess.run(["git", "-C", str(src), "diff", f"{pre}..{fix}", "--", "src/"],
                       capture_output=True, text=True)
    if d.returncode != 0:
        raise Unresolved(f"{task}: git diff {pre}..{fix} failed in {src}: "
                         f"{(d.stderr or '').strip().splitlines()[-1:]}")
    if not d.stdout.strip():
        raise Unresolved(f"{task}: git diff {pre}..{fix} over src/ is empty -- the revisions "
                         f"resolve but the fix touches nothing, so the scan would be vacuous")
    plus = "\n".join(l[1:] for l in d.stdout.splitlines()
                     if l.startswith("+") and not l.startswith("+++"))
    minus = "\n".join(l[1:] for l in d.stdout.splitlines()
                      if l.startswith("-") and not l.startswith("---"))
    added, removed = _code_tokens(plus), _code_tokens(minus)
    # "Appears only on the added side" has to mean NEW TO THE CODEBASE, not merely "on a `+`
    # line". Taken literally it matched `click`, `option`, `close`, `before` and `which` --
    # words all over the pre-fix tree and all over ordinary prose -- and flagged 5 to 20 of
    # the 24 memories on every task. That is the opposite failure to the vacuous one and just
    # as uninformative: a check that can never pass says nothing when it fires. A token
    # already present in the pre-fix tree cannot be evidence that a memory carries the fix,
    # so the pre-fix tree itself is the discriminator.
    pre_tree = scratch / f"c45/run-{task}/base/{task}/src"
    if not pre_tree.is_dir():
        raise Unresolved(f"{task}: pre-fix tree {pre_tree} is absent, so 'new to the "
                         f"codebase' cannot be computed and every added word would flag")
    # Deliberately NOT `_code_tokens` here. The two sides are asymmetric on purpose: the
    # added side is narrowed to code, because a hit should mean the memory names something
    # the fix INTRODUCED; the "already in the tree" side is widened to every word in the
    # file, prose included, because any prior occurrence at all disqualifies a token as
    # distinctive. Narrowing this side instead made `invoked` and `behavior` look new when
    # they were sitting in a pre-fix docstring.
    present: set[str] = set()
    for f in pre_tree.rglob("*.py"):
        present |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}",
                                  f.read_text(errors="replace")))
    tokens = added - removed - present
    if not tokens:
        raise Unresolved(f"{task}: the fix diff introduces no identifier absent from the "
                         f"pre-fix tree, so a token scan against it cannot detect anything")
    return tokens


def hidden_check_names(scratch: Path, task: str) -> set[str]:
    """Function names defined in the hidden checks and not in the visible tree.

    RAISES when the checks directory is absent or defines no test function: an empty name set
    makes the scan below match nothing, which reads identically to "no leakage".
    """
    checks = scratch / f"c45/run-{task}/base/checks/{task}"
    if not checks.is_dir():
        raise Unresolved(f"{task}: hidden checks directory {checks} is absent")
    names: set[str] = set()
    for f in checks.rglob("*.py"):
        names |= set(re.findall(r"^def (test_\w+)", f.read_text(errors="replace"), re.M))
    if not names:
        raise Unresolved(f"{task}: {checks} defines no test function, so the scan would be "
                         f"vacuous")
    visible: set[str] = set()
    tree = scratch / f"c45/run-{task}/base/{task}/tests"
    if not tree.is_dir():
        raise Unresolved(f"{task}: visible test tree {tree} is absent, so 'hidden and not "
                         f"visible' cannot be computed")
    for f in tree.rglob("test_*.py"):
        visible |= set(re.findall(r"^def (test_\w+)", f.read_text(errors="replace"), re.M))
    remaining = names - visible
    if not remaining:
        raise Unresolved(f"{task}: every hidden check name is also visible in the tree, so "
                         f"the scan has nothing to look for")
    return remaining


#: The four conditions, as SUBSETS of one frozen corpus rather than four corpora. Holding the
#: task fixed and varying what is in the store removes the task/category confound a
#: one-category-per-task design carries: with four tasks and four categories, every category
#: would also be a different bug.
#:
#:   useful       support + the task's on-subject memories whose claim is TRUE here
#:   unnecessary  support only -- nothing in the store bears on this task's mechanism
#:   stale        support + the task's on-subject memories whose claim is FALSE here
#:   distracting  the whole corpus, including every other task's memories and the distractors
#:
#: `stale` is empty for a task with no refuted on-subject memory, and is reported as empty
#: rather than filled from somewhere else.
CONDITIONS = ("useful", "unnecessary", "stale", "distracting")


def condition_ids(corpus: dict, labels: dict, task: str, condition: str) -> list[str]:
    support = labels[task]["support"]
    distract = [m["id"] for m in corpus["memories"] if m["provenance"] == "distractor"]
    support_only = [i for i in support if i not in distract]
    if condition == "useful":
        return sorted(support_only + labels[task]["useful"])
    if condition == "unnecessary":
        return sorted(support_only)
    if condition == "stale":
        return sorted(support_only + labels[task]["stale"])
    return sorted(m["id"] for m in corpus["memories"])


def condition_digest(corpus: dict, ids: list[str]) -> str:
    import hashlib
    by = {m["id"]: m for m in corpus["memories"]}
    blob = "\n".join(f"{i}\x1f{by[i]['kind']}\x1f{','.join(by[i]['tags'])}\x1f"
                      f"{by[i]['content']}" for i in ids)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def reachability(corpus: Path, out_dir: Path, only: list[str] | None = None,
                 tag: str = "all") -> dict:
    """Seed a store from this corpus and run the arm's own search tool against it."""
    sys.path.insert(0, str(BENCH))
    sys.path.insert(0, str(BENCH.parent.parent / "src"))
    import seed_store
    src = json.loads(corpus.read_text())
    if only is not None:
        src = {**src, "memories": [m for m in src["memories"] if m["id"] in set(only)]}
        corpus = out_dir / f"corpus-{tag}.json"
        corpus.write_text(json.dumps(src, indent=1))
    db = out_dir / f"dev-m1-{tag}.db"
    if db.exists():
        db.unlink()
    rows = seed_store.seed(db, "dev-m1", "agent", corpus)
    by_memory = {r["memory_id"]: r["corpus_id"] for r in rows}
    from nexus_memory.domain.models import Scope, SearchQuery
    from nexus_memory.memory import MemoryService
    from nexus_memory.storage import SQLiteRepository
    svc = MemoryService(SQLiteRepository(db), Scope("dev-m1", "agent"), None, None)
    out = {}
    for task, q in QUERIES.items():
        page = svc.search(SearchQuery(query=q, limit=20))
        ranked = [{"rank": i, "id": by_memory.get(h.memory_id, "?")}
                  for i, h in enumerate(page.hits, 1)]
        out[task] = {"query": q, "returned": len(ranked), "ranking": ranked}
    return {"store": str(db), "seeded": len(rows), "per_task": out}


def build(scratch: Path, corpus_path: Path, do_reach: bool, out_dir: Path,
          clone: str | None = None) -> dict:
    corpus = json.loads(corpus_path.read_text())
    truth = {}
    for m in corpus["memories"]:
        truth[m["id"]] = {t: probe_tree(m.get("probe"),
                                        scratch / f"c45/run-{t}/base/{t}") for t in TASKS}

    # Each scan reports its own STATUS beside its findings. `{}` used to mean both "looked
    # and found nothing" and "never looked"; they are different answers and are now different
    # values. `unresolved` is never rendered or exited as "no leakage".
    leak_fix, leak_check = {}, {}
    fix_status, check_status = {}, {}
    for t in TASKS:
        try:
            added = fix_added_tokens(scratch, t, clone)
            fix_status[t] = {"state": "measured", "tokens_scanned": len(added)}
        except Unresolved as e:
            added, fix_status[t] = set(), {"state": "unresolved", "reason": str(e)}
        try:
            hidden = hidden_check_names(scratch, t)
            check_status[t] = {"state": "measured", "names_scanned": len(hidden)}
        except Unresolved as e:
            hidden, check_status[t] = set(), {"state": "unresolved", "reason": str(e)}
        for m in corpus["memories"]:
            words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", m["content"]))
            if added and (hit := sorted(words & added)):
                leak_fix.setdefault(t, {})[m["id"]] = hit
            if hidden and (hit := sorted(words & hidden)):
                leak_check.setdefault(t, {})[m["id"]] = hit

    labels = {}
    for t in TASKS:
        on_subject = [m["id"] for m in corpus["memories"] if t in SUBJECT.get(m["id"], [])]
        useful = [i for i in on_subject if truth[i][t] == "true"]
        stale = [i for i in on_subject if truth[i][t] == "false"]
        unsettled = [i for i in on_subject if truth[i][t] == "unknown"]
        cats = []
        if useful:
            cats.append("useful")
        if stale:
            cats.append("stale")
        if not on_subject:
            cats.append("unnecessary")
        labels[t] = {"categories": cats or ["unnecessary"], "on_subject": on_subject,
                     "useful": useful, "stale": stale, "unsettled": unsettled,
                     "support": [m["id"] for m in corpus["memories"]
                                 if not SUBJECT.get(m["id"], [])]}

    rep = {"corpus": str(corpus_path), "corpus_version": corpus["corpus_version"],
           "scratch": str(scratch), "truth_at_checkout": truth, "labels": labels,
           "fix_leakage": leak_fix, "hidden_check_leakage": leak_check,
           "fix_leakage_status": fix_status, "hidden_check_leakage_status": check_status,
           "leakage_scan_scope": (
               "Identifier-level scans only. They compare tokens; they do not establish that "
               "no memory conveys a fix SEMANTICALLY, in different words. Provenance review "
               "is what covers that, and is recorded in FREEZE-dev-m1.md rather than here."),
           "unresolved_scans": sorted(
               [f"fix_leakage/{t}" for t in TASKS if fix_status[t]["state"] != "measured"]
               + [f"hidden_check_leakage/{t}" for t in TASKS
                  if check_status[t]["state"] != "measured"])}
    if do_reach:
        rep["reachability"] = reachability(corpus_path, out_dir)
        conds = {}
        for t in TASKS:
            for c in CONDITIONS:
                ids = condition_ids(corpus, labels, t, c)
                r = reachability(corpus_path, out_dir, ids, f"{t}-{c}")["per_task"][t]
                ranks = {h["id"]: h["rank"] for h in r["ranking"]}
                conds[f"{t}/{c}"] = {
                    "memories": ids, "n": len(ids),
                    "digest": condition_digest(corpus, ids),
                    "returned": r["returned"],
                    "useful_ranks": {i: ranks.get(i) for i in labels[t]["useful"]
                                     if i in ids},
                    "stale_ranks": {i: ranks.get(i) for i in labels[t]["stale"] if i in ids},
                    "top4": [h["id"] for h in r["ranking"][:4]],
                }
        rep["conditions"] = conds
        for t in TASKS:
            r = rep["reachability"]["per_task"][t]
            ranks = {h["id"]: h["rank"] for h in r["ranking"]}
            labels[t]["useful_ranks"] = {i: ranks.get(i) for i in labels[t]["useful"]}
            labels[t]["stale_ranks"] = {i: ranks.get(i) for i in labels[t]["stale"]}
            on = set(labels[t]["useful"]) | set(labels[t]["stale"])
            top = [h["id"] for h in r["ranking"][:4]]
            labels[t]["distracting"] = bool(r["ranking"]) and len(set(top) & on) < len(top)
    return rep


def render(rep: dict) -> str:
    L = ["=" * 96,
         f"corpus {rep['corpus_version']} -- measured labels (model-free)", "=" * 96, ""]
    L.append(f"{'memory':8} {'k1':>7} {'k2':>7} {'k3':>7} {'k4':>7}   subject")
    for mid, row in rep["truth_at_checkout"].items():
        L.append(f"{mid:8} " + " ".join(f"{row[t]:>7}" for t in TASKS)
                 + "   " + (", ".join(SUBJECT.get(mid, [])) or "-"))
    L += ["", "LABELS (derived from the table above, not asserted)"]
    for t in TASKS:
        lab = rep["labels"][t]
        L.append(f"  {t}: {'+'.join(lab['categories'])}")
        L.append(f"      useful {lab['useful'] or '-'}   stale {lab['stale'] or '-'}   "
                 f"unsettled {lab['unsettled'] or '-'}")
        if "useful_ranks" in lab:
            L.append(f"      ranks: useful {lab['useful_ranks']}  stale {lab['stale_ranks']}  "
                     f"distracting={lab['distracting']}")
    L += ["", "LEAKAGE -- limited, identifier-level scans; see `leakage_scan_scope`"]
    for label, key, status in (
            ("token added only by the fix", "fix_leakage", "fix_leakage_status"),
            ("hidden check name not visible in the tree", "hidden_check_leakage",
             "hidden_check_leakage_status")):
        L.append(f"  memories containing a {label}:")
        for t in TASKS:
            st = rep[status][t]
            if st["state"] != "measured":
                L.append(f"    {t}: UNRESOLVED -- {st['reason']}")
            else:
                hits = rep[key].get(t)
                n = st.get("tokens_scanned", st.get("names_scanned"))
                L.append(f"    {t}: {hits if hits else 'none'}   (scanned against {n})")
    if rep["unresolved_scans"]:
        L.append(f"  UNRESOLVED SCANS: {rep['unresolved_scans']} -- these are NOT 'no "
                 f"leakage'; no evidence was gathered for them")
    if "reachability" in rep:
        L += ["", "REACHABILITY -- the arm's own search, over a store seeded from this corpus"]
        for t in TASKS:
            r = rep["reachability"]["per_task"][t]
            L.append(f"  {t}: {r['returned']} hit(s)  "
                     f"top: {[h['id'] for h in r['ranking'][:6]]}")
    if "conditions" in rep:
        L += ["", "CONDITIONS -- one frozen corpus, four subsets per task",
              f"  {'cell':22}{'n':>4}{'hits':>6}  {'digest':18}top 4 / useful ranks / stale"]
        for key, c in rep["conditions"].items():
            L.append(f"  {key:22}{c['n']:>4}{c['returned']:>6}  {c['digest']:18}"
                     f"{c['top4']}  useful={c['useful_ranks']}  stale={c['stale_ranks']}")
    return "\n".join(L)


def main(argv: list[str]) -> int:
    """Exit status is a verdict, not a report of whether the script crashed.

      0  every scan ran and found nothing
      1  a scan found leakage -- the corpus carries part of an answer
      3  a scan could not gather its evidence; the answer is UNKNOWN, not "clean"

    The previous version returned 0 in all three cases, which is the same defect as the
    boundary control that excluded `None` from `all_hold`: an absent measurement passing as
    a satisfied one.
    """
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    scratch = Path(argv[1])
    corpus = (Path(argv[argv.index("--corpus") + 1]) if "--corpus" in argv
              else BENCH / "corpus-dev-m1.json")
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    clone = Path(argv[argv.index("--clone") + 1]).as_posix() if "--clone" in argv else None
    rep = build(scratch, corpus, "--no-reach" not in argv,
                (out.parent if out else BENCH), clone)
    if out:
        out.write_text(json.dumps(rep, indent=1) + "\n")
        print(f"wrote {out}", file=sys.stderr)
    print(render(rep))
    if rep["fix_leakage"] or rep["hidden_check_leakage"]:
        print(f"LEAKAGE FOUND: fix={rep['fix_leakage']} "
              f"checks={rep['hidden_check_leakage']}", file=sys.stderr)
        return 1
    if rep["unresolved_scans"]:
        print(f"UNRESOLVED: {rep['unresolved_scans']} -- refusing to report 'no leakage' "
              f"from a scan that gathered no evidence", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
