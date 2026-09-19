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

    python3 verify_dev_m1.py <scratch> [--corpus <f>] [--out <f>] [--no-reach]
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


def fix_added_tokens(scratch: Path, task: str) -> set[str]:
    """Distinctive identifiers appearing only on the ADDED side of the task's own fix."""
    base = scratch / f"c45/run-{task}/base"
    spec = json.loads((base / "fixtures.json").read_text())
    entry = spec[0] if isinstance(spec, list) else spec
    fix, pre = entry.get("fix"), entry.get("pre_fix")
    clone = entry.get("clone") or entry.get("source_clone")
    if not (fix and pre and clone and Path(clone).is_dir()):
        return set()
    d = subprocess.run(["git", "-C", str(clone), "diff", f"{pre}..{fix}", "--", "src/"],
                       capture_output=True, text=True)
    added, removed = set(), set()
    for line in d.stdout.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", line))
        elif line.startswith("-") and not line.startswith("---"):
            removed |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", line))
    return added - removed


def hidden_check_names(scratch: Path, task: str) -> set[str]:
    checks = scratch / f"c45/run-{task}/base/checks/{task}"
    names: set[str] = set()
    for f in checks.rglob("*.py"):
        names |= set(re.findall(r"^def (test_\w+)", f.read_text(errors="replace"), re.M))
    visible: set[str] = set()
    tree = scratch / f"c45/run-{task}/base/{task}/tests"
    for f in tree.rglob("test_*.py"):
        visible |= set(re.findall(r"^def (test_\w+)", f.read_text(errors="replace"), re.M))
    return names - visible


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


def build(scratch: Path, corpus_path: Path, do_reach: bool, out_dir: Path) -> dict:
    corpus = json.loads(corpus_path.read_text())
    truth = {}
    for m in corpus["memories"]:
        truth[m["id"]] = {t: probe_tree(m.get("probe"),
                                        scratch / f"c45/run-{t}/base/{t}") for t in TASKS}

    leak_fix, leak_check = {}, {}
    for t in TASKS:
        added = fix_added_tokens(scratch, t)
        hidden = hidden_check_names(scratch, t)
        for m in corpus["memories"]:
            words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", m["content"]))
            if hit := sorted(words & added):
                leak_fix.setdefault(t, {})[m["id"]] = hit
            if hit := sorted(words & hidden):
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
           "fix_leakage": leak_fix, "hidden_check_leakage": leak_check}
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
    L += ["", "LEAKAGE"]
    L.append(f"  memories containing a token added only by the fix: "
             f"{rep['fix_leakage'] or 'none'}")
    L.append(f"  memories naming a hidden check not visible in the tree: "
             f"{rep['hidden_check_leakage'] or 'none'}")
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
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    scratch = Path(argv[1])
    corpus = (Path(argv[argv.index("--corpus") + 1]) if "--corpus" in argv
              else BENCH / "corpus-dev-m1.json")
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    rep = build(scratch, corpus, "--no-reach" not in argv,
                (out.parent if out else BENCH))
    if out:
        out.write_text(json.dumps(rep, indent=1) + "\n")
        print(f"wrote {out}", file=sys.stderr)
    print(render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
