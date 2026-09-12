"""Freeze the captured corpus: export it, digest it, render it, and register what it is.

Step 3 of `capture-policy-a1` §3 and of `freeze-heldout-a1` §2, and the step whose ordering is
the whole point: **a memory written after this runs is seeded, not captured, and voids the
corpus for that task.** Everything here therefore happens before a single held-out task is
revealed, and the artifacts it writes are what the evaluation runs against.

It does five things and refuses to do them by halves:

  export     the store's rows become `corpus-heldout-a1.json` -- the delivered fields only,
             plus any superseded revisions, because arm 2 holds `history` and a corpus record
             that omits them would not describe what that arm can see.
  digest     the row digest, which is the value that goes into `corpus_digest` in the run
             configuration. After that the arm runner refuses to start against any other
             store, which is the only thing that can catch the wrong corpus being configured.
  master     the capture store is copied to a durable location and becomes the master every
             arm-run is given a private copy of. It is copied rather than rebuilt from the
             JSON: `revise` produces history, history is part of what arm 2 can retrieve, and
             a reseed from a flat export would silently drop it.
  render     arm 3's notes file, mechanically, by `render_notes.py` -- same facts, same
             wording, same order (`capture-policy-a1` §4).
  declare    the task-corpus mix, validated against `protocol-a1` §7. The mix cannot be fixed
             before the memories exist, and it must be fixed before any arm runs; this is the
             one moment that is both. A missing category is REPORTED as an unmet requirement,
             never repaired -- repairing it here is precisely the contamination the held-out
             set exists to detect.

Usage:  freeze_corpus.py <scratch> [--mix <mix.json>] [--master <dir>] [--out <dir>]
                                   [--config <config.json>]
        freeze_corpus.py <scratch> --dry-run        # export and validate, write nothing

`--out` defaults to this directory, which is where a real freeze belongs. It exists so the
write path can be exercised against a synthetic capture store without a rehearsal leaving a
file named `corpus-heldout-a1.json` in the place the real corpus goes.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402
import render_notes                                                     # noqa: E402

CORPUS_VERSION = "heldout-a1"
#: The three categories `protocol-a1` §7 requires the mix to contain.
MIX_CATEGORIES = ("useful", "unnecessary", "outdated")
#: The two labels §7b requires every necessary fact to carry.
DISCOVERABILITY = ("discoverable", "absent")


def rows(db: Path, namespace: str, actor: str) -> list[dict]:
    """Every live memory in scope, in the order it was recorded, with its history.

    Ordered by the memory's first revision rather than by id: a corpus reads as a session's
    work in the sequence that session did it, and a uuid order is no order at all.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    live = con.execute(
        "select m.memory_id, m.current_revision_id, r.kind, r.tags_json, b.body,"
        "       (select min(rowid) from revisions where memory_id = m.memory_id) as first_seq "
        "from memories m "
        "join revisions r on r.revision_id = m.current_revision_id "
        "join blobs b on b.id = r.blob_id "
        "where m.tombstoned = 0 and m.namespace = ? and m.actor = ? "
        "order by first_seq", (namespace, actor)).fetchall()
    out = []
    for index, row in enumerate(live, start=1):
        superseded = con.execute(
            "select r.revision_id, r.kind, r.tags_json, b.body from revisions r "
            "join blobs b on b.id = r.blob_id "
            "where r.memory_id = ? and r.revision_id != ? order by r.rowid",
            (row["memory_id"], row["current_revision_id"])).fetchall()
        memory = {
            "id": f"h{index:02d}",
            "memory_id": row["memory_id"],
            "kind": row["kind"],
            "tags": json.loads(row["tags_json"] or "[]"),
            "content": row["body"] if isinstance(row["body"], str)
                       else bytes(row["body"]).decode(),
        }
        if superseded:
            memory["superseded"] = [
                {"revision_id": s["revision_id"], "kind": s["kind"],
                 "tags": json.loads(s["tags_json"] or "[]"),
                 "content": s["body"] if isinstance(s["body"], str)
                            else bytes(s["body"]).decode()}
                for s in superseded]
        out.append(memory)
    con.close()
    return out


def store_digest(db: Path) -> str:
    """Byte-for-byte the arm runner's digest, so one number compares across both."""
    con = sqlite3.connect(db)
    data = list(con.execute(
        "select m.memory_id, m.current_revision_id, m.tombstoned, r.kind, r.tags_json, b.body "
        "from memories m join revisions r on r.revision_id = m.current_revision_id "
        "join blobs b on b.id = r.blob_id order by m.memory_id"))
    con.close()
    return hashlib.sha256(repr(data).encode()).hexdigest()[:16]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_mix(mix: dict | None, corpus: list[dict], tasks: list[str]) -> dict:
    """Check the declaration against `protocol-a1` §7 and §7b. Reports; never repairs.

    Three findings are possible and all three are results rather than errors: a category with
    no instance, a task with no declaration, and a necessary fact carrying no discoverability
    label. `freeze-heldout-a1` §2 is explicit that a missing outdated instance is reported as
    an unmet mix requirement -- not fixed by editing a memory, re-selecting tasks, or
    capturing again with the held-out tasks in view.
    """
    if mix is None:
        return {"declared": False,
                "unmet": ["no mix declaration was supplied; the corpus is frozen without one "
                          "and no arm may run until one is declared and hashed"]}
    known = {m["id"] for m in corpus}
    per_task = mix.get("tasks", {})
    unmet, notes = [], []

    unknown = sorted({mid for task in per_task.values()
                      for mid in task.get("relevance", {}) if mid not in known})
    if unknown:
        unmet.append(f"declaration names memories that are not in the corpus: "
                     f"{', '.join(unknown)}")

    missing_tasks = [t for t in tasks if t not in per_task]
    if missing_tasks:
        unmet.append(f"no mix category declared for: {', '.join(missing_tasks)}")

    declared = {t: per_task[t].get("memory") for t in per_task}
    bad = {t: c for t, c in declared.items() if c not in MIX_CATEGORIES}
    if bad:
        unmet.append(f"category must be one of {MIX_CATEGORIES}: {bad}")
    for category in MIX_CATEGORIES:
        holders = [t for t, c in declared.items() if c == category]
        if not holders:
            unmet.append(f"the mix contains no task where captured memory is {category!r} "
                         f"(protocol-a1 section 7 requires all three)")
        else:
            notes.append(f"{category}: {', '.join(holders)}")

    labelled, unlabelled = [], []
    for task, spec in per_task.items():
        for mid, relevance in spec.get("relevance", {}).items():
            if relevance != "necessary":
                continue
            label = spec.get("discoverability", {}).get(mid)
            (labelled if label in DISCOVERABILITY else unlabelled).append((task, mid, label))
    seen = {label for _, _, label in labelled}
    if unlabelled:
        unmet.append(f"necessary facts with no discoverability label (section 7b): "
                     f"{[(t, m) for t, m, _ in unlabelled]}")
    if labelled and len(seen) < 2:
        notes.append(f"only {', '.join(sorted(seen))} necessary facts are declared: section 7b "
                     f"says this set can measure retrieval "
                     f"{'efficiency' if 'discoverable' in seen else 'necessity'} and not the "
                     f"other. Reported, not corrected.")
    return {"declared": True, "categories": declared, "unmet": unmet, "notes": notes,
            "necessary_facts": len(labelled) + len(unlabelled)}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-2])
        return 2
    scratch = Path(argv[1])
    dry = "--dry-run" in argv
    mix_path = Path(argv[argv.index("--mix") + 1]) if "--mix" in argv else None
    cfg = a1_config.load(argv[argv.index("--config") + 1] if "--config" in argv else None)
    cfg = cfg.require()
    master_dir = Path(argv[argv.index("--master") + 1]) if "--master" in argv \
        else Path(cfg.source_clone).parent / "heldout-corpus"
    out_dir = Path(argv[argv.index("--out") + 1]) if "--out" in argv else BENCH
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = scratch / "capture"
    store = cap / "store" / "nexus.db"
    capture_records = cap / "capture-records.json"
    if not store.exists():
        print(f"no capture store at {store}; run run_capture.py first")
        return 1
    if not capture_records.exists():
        print(f"no capture records at {capture_records}; the capture run did not finish, and a "
              f"corpus without its capture provenance is not a frozen corpus")
        return 1

    captured = json.loads(capture_records.read_text())
    memories = rows(store, cfg.namespace, cfg.actor)
    digest = store_digest(store)
    revised = [m["id"] for m in memories if "superseded" in m]

    tasks = [t["task"] for t in
             json.loads((BENCH / "tasks-capture-a1.json").read_text())["tasks"]]
    heldout_tasks = sorted((json.loads(mix_path.read_text()).get("tasks", {}) if mix_path
                            else {}).keys())
    mix = json.loads(mix_path.read_text()) if mix_path else None
    verdict = validate_mix(mix, memories, heldout_tasks or [])

    corpus = {
        "corpus_version": CORPUS_VERSION,
        "status": "frozen" if not dry else "dry run, nothing written",
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "policy": "capture-policy-a1.md",
        "registration": "freeze-heldout-a1.md section 2",
        "repository": "pallets/click",
        "capture_tasks": tasks,
        "provenance": {
            "captured_by": "run_capture.py, a prior-session agent run under "
                           "capture-policy-a1 section 3, which saw neither the held-out tasks "
                           "nor that they exist",
            "capture_records": str(capture_records),
            "capture_records_sha256": sha256_file(capture_records),
            "capture_store": str(store),
            "store_digest": digest,
            "capture_tools": captured.get("capture_tools"),
            "per_task": [{"task": r["task"], "terminal": r.get("terminal"),
                          "checks": r.get("scored", {}).get("summary"),
                          "memory_calls": r.get("memory", {}).get("by_tool"),
                          "failed_memory_calls": len(r.get("memory", {})
                                                     .get("failed_calls", []))}
                         for r in captured.get("records", [])],
        },
        "exposure": "Captured blind. No author of this corpus had read the held-out tasks, "
                    "and the session that wrote it was not told they exist. This is what the "
                    "development corpus could not claim (capture-policy-a1 section 5).",
        "delivered_metadata_note":
            "Tags are DELIVERED -- they appear in search hits, in get responses and in the "
            "rendered notes. Evaluator vocabulary must never sit in them. The mix lives in "
            "the declaration beside this file, never in a memory.",
        "superseded_note":
            "A memory with `superseded` was revised during capture. Arm 2 holds `history` and "
            "can retrieve those revisions, so they are rendered for arm 3 as well; the two "
            "arms must differ in access mechanism and nothing else (capture-policy-a1 "
            "section 4).",
        "memories": memories,
    }

    print(f"capture store   {store}")
    print(f"  memories      {len(memories)} live, {len(revised)} with superseded revisions")
    print(f"  row digest    {digest}")
    print(f"  capture runs  " + ", ".join(
        f"{r['task']}={r.get('terminal', {}).get('verdict', '?')}"
        for r in captured.get("records", [])))
    print(f"\nmix declaration: {'none supplied' if mix is None else mix_path}")
    for line in verdict.get("notes", []):
        print(f"  note   {line}")
    for line in verdict["unmet"]:
        print(f"  UNMET  {line}")
    if not verdict["unmet"]:
        print("  all of protocol-a1 section 7's categories have an instance")

    if dry:
        print("\ndry run: nothing written")
        return 0 if not verdict["unmet"] else 1

    corpus_path = out_dir / f"corpus-{CORPUS_VERSION}.json"
    notes_path = out_dir / f"notes-{CORPUS_VERSION}.md"
    if corpus_path.exists():
        print(f"\nrefusing to overwrite {corpus_path.name}: a corpus is frozen once. Move the "
              f"existing one aside deliberately if it is being superseded.")
        return 1
    corpus_path.write_text(json.dumps(corpus, indent=1, ensure_ascii=False) + "\n")
    text, withheld = render_notes.render(corpus)
    notes_path.write_text(text)

    master_dir.mkdir(parents=True, exist_ok=True)
    master = master_dir / "nexus-heldout.db"
    if master.exists():
        print(f"\nrefusing to overwrite the master store at {master}")
        return 1
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(store) + suffix)
        if source.exists():
            shutil.copy2(source, str(master) + suffix)
    master_digest = store_digest(master)

    record = {
        "corpus": str(corpus_path), "corpus_sha256": sha256_file(corpus_path),
        "notes": str(notes_path), "notes_sha256": sha256_file(notes_path),
        "notes_withheld_fields": withheld,
        "master_store": str(master), "master_digest": master_digest,
        "capture_store_digest": digest,
        "master_matches_capture": master_digest == digest,
        "memories": len(memories), "memories_with_history": revised,
        "mix_declaration": str(mix_path) if mix_path else None,
        "mix_declaration_sha256": sha256_file(mix_path) if mix_path else None,
        "mix": verdict,
        "frozen_utc": corpus["frozen_utc"],
        "config_to_set": {"store_master": str(master), "corpus_digest": master_digest,
                          "corpus_size": len(memories), "notes_file": notes_path.name},
        "next": ["set the four configuration values above in a1-config.json",
                 "only then reveal the held-out tasks and run the arms"],
    }
    freeze_path = out_dir / f"freeze-record-{CORPUS_VERSION}.json"
    freeze_path.write_text(json.dumps(record, indent=1) + "\n")

    print(f"\ncorpus   -> {corpus_path.name}  ({len(memories)} memories)")
    print(f"notes    -> {notes_path.name}  (withheld: {', '.join(withheld) or 'none'})")
    print(f"master   -> {master}  digest {master_digest} "
          f"({'matches' if master_digest == digest else 'DOES NOT MATCH'} the capture store)")
    print(f"record   -> {freeze_path.name}")
    print(f"\nSet in a1-config.json before any arm runs:")
    print(f'  "store_master": "{master}",')
    print(f'  "corpus_digest": "{master_digest}",')
    # corpus_size is the reachability gate's expected count: the runner aborts an arm whose
    # own probe sees a different number. It was pinned at the development corpus's 13, which
    # would have failed every held-out nexus arm before a token was spent -- loudly, but 36
    # runs into a frozen schedule.
    print(f'  "corpus_size": {len(memories)},')
    print(f'  "notes_file": "{notes_path.name}",')
    if verdict["unmet"]:
        print("\nThe corpus is frozen and the mix requirement is UNMET, above. That is a "
              "reported result, not a thing to fix by capturing again.")
    return 0 if master_digest == digest else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
