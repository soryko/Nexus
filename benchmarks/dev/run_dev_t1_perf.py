"""T1 performance and storage measurement — development data only.

Measures the registered promotion gates for each index profile against the same two
fixtures. The fixture is built once per size under the `exact` profile and then copied
for each profile, so load order, content and durability settings are identical across a
pair by construction rather than by intention.

Usage:  .venv-sqlite/bin/python benchmarks/dev/run_dev_t1_perf.py [--sizes 1000,10000]
"""
from __future__ import annotations

import json
import random
import shutil
import statistics
import subprocess
import sys
import sqlite3
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nexus_memory.domain.models import MemoryInput, Scope, SearchQuery  # noqa: E402
from nexus_memory.memory import MemoryService  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402
from run_dev_t1 import (  # noqa: E402
    DELIVERED_BYTES, DELIVERED_ITEMS, HISTORY_MEMORIES, HISTORY_REVISIONS, POOL_LIMIT, index_sizes,
)

SEED = 20260906
PROFILES = ("exact", "stem", "dual", "split")
BLOCKS = 3
QUERIES_PER_BLOCK = 200
WARMUP_QUERIES = 50
MEASURED_WRITES = 200
WARMUP_WRITES = 50
LONG_HISTORY_FRACTION = 0.02
LONG_HISTORY_REVISIONS = 25  # above the 20-revision expansion cap, so the cap binds

# Prose written in one morphological form and queried in another, so the stemmed
# indexes are exercised at scale rather than only on the 24-item development corpus.
SUBJECTS = ["caption", "thumbnail", "manifest", "rendition", "segment", "sprite", "ladder",
            "mezzanine", "playlist", "watermark", "transcript", "poster", "chapter", "bitrate"]
VERB_INDEXED = ["expires", "publishes", "throttles", "migrates", "notifies", "regenerates",
                "validates", "schedules", "encodes", "restores"]
VERB_QUERIED = ["expire", "publishing", "throttle", "migration", "notification", "regenerate",
                "validation", "scheduling", "encoding", "restoration"]
TAGS = ["captions", "encoding", "storage", "playback", "ingest", "cdn", "licensing", "pipeline"]
IDENTIFIERS = ["transcode_worker.py", "caption_track_id", "AssetState.PUBLISHED",
               "/v3/assets/manifest", "manifest_builder.py", "rendition_id", "mux2"]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index] * 1000, 4)  # milliseconds


def summarise(values: list[float]) -> dict:
    return {"n": len(values), "p50": percentile(values, 0.50), "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99), "mean_ms": round(statistics.fmean(values) * 1000, 4)}


def body(rng: random.Random, index: int) -> str:
    subject = rng.choice(SUBJECTS)
    verb = rng.choice(VERB_INDEXED)
    identifier = rng.choice(IDENTIFIERS)
    filler = " ".join(rng.choice(SUBJECTS + VERB_INDEXED) for _ in range(rng.randint(10, 90)))
    return f"The {subject} {verb} after a fixed interval; see {identifier}. Record {index}. {filler}."


def build_fixture(path: Path, size: int) -> dict:
    rng = random.Random(SEED)
    service = MemoryService(SQLiteRepository(path), Scope("perf", "local"))
    lengths: list[int] = []
    history_lengths: list[int] = []
    tag_counts: list[int] = []
    long_slice = max(1, int(size * LONG_HISTORY_FRACTION))
    for index in range(size):
        revisions = LONG_HISTORY_REVISIONS if index < long_slice else (3 if rng.random() < 0.55 else 2)
        history_lengths.append(revisions)
        tags = tuple(rng.sample(TAGS, rng.randint(1, 3)))
        tag_counts.append(len(tags))
        content = body(rng, index)
        lengths.append(len(content.encode("utf-8")))
        receipt = service.record(MemoryInput(content, kind="observation", tags=tags), f"seed-{index}-0")
        for step in range(1, revisions):
            content = body(rng, index)
            lengths.append(len(content.encode("utf-8")))
            receipt = service.revise(receipt.memory_id, receipt.revision_id,
                                     MemoryInput(content, kind="observation", tags=tags),
                                     f"seed-{index}-{step}")
    return {
        "memories": size,
        "revisions": sum(history_lengths),
        "revisions_per_memory_mean": round(sum(history_lengths) / size, 3),
        "long_history_memories": long_slice,
        "long_history_revisions": LONG_HISTORY_REVISIONS,
        "content_bytes": {"min": min(lengths), "p50": sorted(lengths)[len(lengths) // 2],
                          "p95": sorted(lengths)[int(0.95 * (len(lengths) - 1))], "max": max(lengths),
                          "mean": round(statistics.fmean(lengths), 1)},
        "tags_per_memory": {"min": min(tag_counts), "mean": round(statistics.fmean(tag_counts), 2),
                            "max": max(tag_counts)},
        "history_lengths": {"min": min(history_lengths), "mean": round(statistics.fmean(history_lengths), 3),
                            "max": max(history_lengths)},
    }


def query_set(count: int) -> list[str]:
    rng = random.Random(SEED + 1)
    queries = []
    for _ in range(count):
        shape = rng.random()
        if shape < 0.35:
            queries.append(f"{rng.choice(SUBJECTS)} {rng.choice(VERB_QUERIED)}")
        elif shape < 0.60:
            queries.append(rng.choice(VERB_QUERIED))
        elif shape < 0.80:
            queries.append(f"{rng.choice(SUBJECTS)} {rng.choice(SUBJECTS)}")
        elif shape < 0.92:
            queries.append(rng.choice(IDENTIFIERS))
        else:
            queries.append(f"{rng.choice(SUBJECTS)} {rng.choice(IDENTIFIERS)}")
    return queries


def retrieve(service: MemoryService, text: str) -> None:
    """The budgeted retrieval path: pool, bounded history expansion, bounded delivery."""
    page = service.search(SearchQuery(query=text, limit=POOL_LIMIT))
    expandable = [hit.memory_id for hit in page.hits if hit.has_earlier_revisions][:HISTORY_MEMORIES]
    for memory_id in expandable:
        service.history(memory_id, limit=HISTORY_REVISIONS)
    delivered_bytes = 0
    for count, hit in enumerate(page.hits):
        if count >= DELIVERED_ITEMS:
            break
        view = service.get(hit.memory_id)
        size = len(view.content.encode("utf-8"))
        if delivered_bytes + size > DELIVERED_BYTES:
            break
        delivered_bytes += size


def measure_profile(fixture: Path, profile: str, queries: list[str], size: int) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "memory.sqlite3"
        shutil.copy(fixture, path)
        for suffix in ("-wal", "-shm"):
            source = Path(str(fixture) + suffix)
            if source.exists():
                shutil.copy(source, Path(str(path) + suffix))

        started = time.perf_counter()
        service = MemoryService(SQLiteRepository(path, index_profile=profile), Scope("perf", "local"))
        open_seconds = time.perf_counter() - started

        for text in queries[:WARMUP_QUERIES]:
            retrieve(service, text)

        blocks = []
        cursor = WARMUP_QUERIES
        for _ in range(BLOCKS):
            block = queries[cursor:cursor + QUERIES_PER_BLOCK]
            cursor += QUERIES_PER_BLOCK
            timings = []
            for text in block:
                started = time.perf_counter()
                retrieve(service, text)
                timings.append(time.perf_counter() - started)
            blocks.append(summarise(timings))

        record_timings = []
        revise_timings = []
        rng = random.Random(SEED + 2)
        # Unmeasured write warm-up. Without it the first profile measured absorbs the
        # cold-cache cost of the write path and looks slower than the ones after it,
        # which would read as a profile difference rather than an ordering artefact.
        for index in range(WARMUP_WRITES):
            warm = service.record(MemoryInput(body(rng, 30_000_000 + index), kind="observation",
                                              tags=("perf",)), f"warm-record-{index}")
            service.revise(warm.memory_id, warm.revision_id,
                           MemoryInput(body(rng, 40_000_000 + index), kind="observation", tags=("perf",)),
                           f"warm-revise-{index}")
        for index in range(MEASURED_WRITES):
            content = body(rng, 10_000_000 + index)
            started = time.perf_counter()
            receipt = service.record(MemoryInput(content, kind="observation", tags=("perf",)),
                                     f"measure-record-{index}")
            record_timings.append(time.perf_counter() - started)
            started = time.perf_counter()
            service.revise(receipt.memory_id, receipt.revision_id,
                           MemoryInput(body(rng, 20_000_000 + index), kind="observation", tags=("perf",)),
                           f"measure-revise-{index}")
            revise_timings.append(time.perf_counter() - started)

        sizes = index_sizes(path)

    pooled = [value for block in blocks for value in [block["p95"]]]
    return {
        "profile": profile,
        "fixture_size": size,
        "open_and_profile_build_seconds": round(open_seconds, 3),
        "retrieval_blocks": blocks,
        "retrieval_p95_worst_block_ms": max(pooled),
        "record": summarise(record_timings),
        "revise": summarise(revise_timings),
        "index_bytes": sizes,
    }


def main() -> None:
    sizes = [1000, 10000]
    for argument in sys.argv[1:]:
        if argument.startswith("--sizes"):
            sizes = [int(value) for value in argument.split("=", 1)[1].split(",")]
    queries = query_set(WARMUP_QUERIES + BLOCKS * QUERIES_PER_BLOCK)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    record = {
        "experiment": "T1 morphology — performance and storage",
        "development_data": True,
        "not_a_v2_result": True,
        "build_commit": commit,
        "interpreter": sys.executable,
        "sqlite_version": sqlite3.sqlite_version,
        "durability": {"journal_mode": "WAL", "synchronous": "FULL"},
        "seed": SEED,
        "protocol": {"blocks": BLOCKS, "queries_per_block": QUERIES_PER_BLOCK,
                     "warmup_queries": WARMUP_QUERIES, "measured_writes": MEASURED_WRITES, "warmup_writes": WARMUP_WRITES,
                     "fixture_built_once_per_size_then_copied": True},
        "fixtures": {},
        "measurements": [],
    }
    for size in sizes:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.sqlite3"
            print(f"building fixture: {size} memories …", flush=True)
            started = time.perf_counter()
            record["fixtures"][str(size)] = build_fixture(fixture, size)
            record["fixtures"][str(size)]["build_seconds"] = round(time.perf_counter() - started, 2)
            for profile in PROFILES:
                print(f"  measuring {profile} …", flush=True)
                record["measurements"].append(measure_profile(fixture, profile, queries, size))
    out = Path(__file__).resolve().parent / "results-dev-t1-perf.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
