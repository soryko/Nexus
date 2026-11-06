"""Installed-path check: the built wheel, a fresh environment, the scope integration, nothing from the source tree.

    uv run --group atlas-ui python benchmarks/atlas/installed_check.py [--out report.json]

1. build the wheel and install it into a new disposable virtual environment (never the live one);
2. copy the project-scope integration to a disposable ``bin`` (a stand-in for the installed one);
3. enroll a disposable checkout through that copy, using the installed interpreter in isolated mode;
4. launch the viewer through it from a directory outside the source tree, drive find -> open -> hub -> Back;
5. hash the store, its -wal/-shm files and the registry before and after; any difference fails the check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import fixture as fx  # noqa: E402
import run as bench  # noqa: E402

INTEGRATION_FILES = ("launcher.py", "project_scope.py", "antigravity_hook.py", "antigravity_mcp.py")


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def snapshot(database: Path, registry: Path) -> dict:
    files = [database, Path(str(database) + "-wal"), Path(str(database) + "-shm"), registry]
    return {p.name.split(".", 1)[-1] if p is not registry else "registry.json":
            {"exists": p.exists(), "bytes": p.stat().st_size if p.exists() else None, "sha256": digest(p),
             "mtime_ns": p.stat().st_mtime_ns if p.exists() else None} for p in files}


def at_rest(database: Path) -> None:
    """Checkpoint and close so the store is a single file, as it is when no process holds it."""
    import gc
    import sqlite3
    gc.collect()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    gc.collect()
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--memories", type=int, default=600)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="atlas-installed-"))
    os.chmod(work, 0o700)
    os.umask(0o077)
    report: dict = {"work_directory": str(work), "steps": {}}

    wheelhouse = work / "wheel"
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(wheelhouse), str(ROOT)], check=True, capture_output=True)
    (wheel,) = wheelhouse.glob("nexus_memory-*.whl")
    venv = work / "venv"
    subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True)
    python = str(venv / "bin" / "python")
    subprocess.run(["uv", "pip", "install", "--python", python, str(wheel)], check=True, capture_output=True)
    where = subprocess.run([python, "-I", "-c", "import nexus_memory, pathlib; print(pathlib.Path(nexus_memory.__file__).parent)"],
                           capture_output=True, text=True, check=True, cwd=work).stdout.strip()
    assert str(venv) in where and str(ROOT / "src") not in where, f"not the installed wheel: {where}"
    static = sorted(p.name for p in (Path(where) / "atlas" / "static").iterdir())
    report["steps"]["wheel"] = {"file": wheel.name, "bytes": wheel.stat().st_size, "installed_package_dir": where, "static_files": static}

    bin_dir = work / "bin"
    bin_dir.mkdir(mode=0o700)
    for name in INTEGRATION_FILES:
        shutil.copyfile(ROOT / "integrations" / "project_scope" / name, bin_dir / name)
        os.chmod(bin_dir / name, 0o600)
    launcher = bin_dir / "launcher.py"

    subject = bench.Subject(work, "installed", integration_dir=bin_dir)
    enrolled = subprocess.run([python, "-I", str(launcher), "enroll", str(subject.checkout)], capture_output=True, text=True,
                              env=subject.env(), cwd=work)
    report["steps"]["enroll_again_via_installed_launcher"] = {"returncode": enrolled.returncode, "stdout": enrolled.stdout.strip()[:200]}
    subject.build(fx.Spec(memories=args.memories, hub_citers=max(1, args.memories // 8), hub_min=max(1, args.memories // 10),
                          revised=max(1, args.memories // 20), forgotten=2, foreign=max(1, args.memories // 30)), progress=lambda m: None)
    registry = subject.database.parent / "registry.json"

    at_rest(subject.database)
    before = snapshot(subject.database, registry)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        viewer = bench.Viewer(subject, python, launcher, work)
        browser, page = bench.new_page(playwright)
        try:
            page.goto(viewer.launch_file.as_uri())
            bench.wait_overview(page)
            status = page.inner_text("#status")
            target = subject.manifest["strata"]["hub"][2]
            bench.search_for(page, target["token"], "1 matching", target["token"])
            first = bench.act(page, action="select")
            hub = bench.act(page, action="hub-open")
            nxt = bench.act(page, action="hub-next")
            chosen = bench.act(page, action="hub-open-last", skip=target["token"])
            back = bench.act(page, action="back", expect=first["focus"])
            journey = {"status": status, "selection_ms": round(first["ms"], 1), "hub_open_ms": round(hub["ms"], 1),
                       "hub_next_ms": round(nxt["ms"], 1), "open_last_ms": round(chosen["ms"], 1), "back_ms": round(back["ms"], 1)}
        finally:
            browser.close()
            viewer.stop()
    after = snapshot(subject.database, registry)
    report["steps"]["journey"] = journey
    report["store_hashes_before"] = before
    report["store_hashes_after"] = after
    names = list(before)
    main_name = next(n for n in names if n.endswith("sqlite3"))
    report["strict_identical_including_sidecars_and_mtimes"] = before == after
    # Data-bearing files: the database and the registry byte for byte, and no committed frame left in a write-ahead log.
    # The -shm file is SQLite's shared-memory index; any reader of a WAL database updates it, and it holds no data.
    wal_name = next(n for n in names if n.endswith("-wal"))
    report["store_unchanged"] = (before[main_name]["sha256"] == after[main_name]["sha256"]
                                 and before["registry.json"]["sha256"] == after["registry.json"]["sha256"]
                                 and (after[wal_name]["bytes"] or 0) == 0)
    out = args.out or (work / "installed-check.json")
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps({k: report[k] for k in ("steps", "store_unchanged", "strict_identical_including_sidecars_and_mtimes", "store_hashes_before", "store_hashes_after")}, indent=2))
    if not report["store_unchanged"]:
        print("STORE CHANGED:", json.dumps({"before": before, "after": after}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
