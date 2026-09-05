"""Reproducible synthetic exact-store baseline; no relevance or MCP latency claim."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import sqlite3
import statistics
import tempfile
from dataclasses import asdict
from pathlib import Path
from time import perf_counter_ns

from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.memory.service import MemoryService
from nexus_memory.storage.sqlite import SQLiteRepository


def quantiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "median_ms": statistics.median(ordered),
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "p99_ms": ordered[math.ceil(0.99 * len(ordered)) - 1],
    }


def body(index: int) -> str:
    # Deterministic distinct 1 KiB ASCII bodies; no embedding/model dependency.
    return "".join(hashlib.sha256(f"{index}:{part}".encode()).hexdigest() for part in range(16))


def measure(directory: Path, name: str, count: int, unique: int, reads: int) -> dict:
    path = directory / f"{name}.sqlite3"
    service = MemoryService(SQLiteRepository(path), Scope("synthetic-benchmark"))
    payloads = [body(i) for i in range(unique)]
    receipts = []
    write_times = []
    for i in range(count):
        item = MemoryInput(payloads[i % unique], tags=("benchmark",))
        started = perf_counter_ns()
        receipts.append(service.record(item, f"write-{i}"))
        write_times.append((perf_counter_ns() - started) / 1_000_000)

    rng = random.Random(20260905)
    read_times = []
    for _ in range(reads):
        index = rng.randrange(count)
        started = perf_counter_ns()
        result = service.get(receipts[index].memory_id)
        read_times.append((perf_counter_ns() - started) / 1_000_000)
        if result.content != payloads[index % unique]:
            raise AssertionError("Exact retrieval returned different bytes")

    sizes = {
        suffix or "main": Path(str(path) + suffix).stat().st_size
        if Path(str(path) + suffix).exists() else 0
        for suffix in ("", "-wal", "-shm")
    }
    with sqlite3.connect(path) as connection:
        blob_count, blob_bytes = connection.execute(
            "SELECT count(*), coalesce(sum(length(body)), 0) FROM blobs"
        ).fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    connection.close()
    if (blob_count, blob_bytes, integrity) != (unique, unique * 1024, "ok"):
        raise AssertionError("Unexpected storage/integrity result")

    return {
        "name": name,
        "records": count,
        "unique_bodies": unique,
        "body_bytes_per_record": 1024,
        "exact_get_samples": reads,
        "record": quantiles(write_times),
        "get": quantiles(read_times),
        "stored_unique_body_bytes": blob_bytes,
        "raw_repeated_body_bytes": count * 1024,
        "file_bytes_at_measurement": sizes,
        "total_file_bytes_at_measurement": sum(sizes.values()),
        "integrity_check": integrity,
        "status": asdict(service.status()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=1000)
    parser.add_argument("--reads", type=int, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.records < 10 or args.reads < 100:
        parser.error("use at least 10 records and 100 read samples")

    with tempfile.TemporaryDirectory(prefix="nexus-benchmark-") as temporary:
        results = {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
            "seed": 20260905,
            "method": "Sequential local service calls; full-sync commits; warm OS cache; nearest-rank tail quantiles.",
            "limits": "Synthetic bodies, no relevance labels, no MCP or queue timing, no cold-cache control, no peak disk/RSS measurement; samples are not latency guarantees.",
            "cases": [
                measure(Path(temporary), "unique", args.records, args.records, args.reads),
                measure(Path(temporary), "repeated", args.records, max(1, args.records // 10), args.reads),
            ],
        }
    encoded = json.dumps(results, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
