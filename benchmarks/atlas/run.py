"""Memory Atlas benchmark: a disposable 10,000-memory project, the real launcher, a real Chromium.

    uv run --group atlas-ui python benchmarks/atlas/run.py --memories 10000 --interactions 100

Everything is measured in the browser against the viewer launched through ``launcher.py atlas``. Targets are
printed beside measured values and never mixed with them. Nothing here tunes a result: the only gate is the
fixture contract (``fixture.check_fixture``), which is checked before any timing and fails the run if unmet.

Definitions (also written to the report):
  * percentiles use the nearest-rank method; spread is max - min.
  * "selection" = click on a result to the first neighborhood drawn plus the inspector filled, then two animation
    frames: the neighbors response has arrived, the focus node changed, and the page has painted twice. Two frames at
    60 Hz are about 33 ms, a floor under every timing here; ``summary_data_ready_before_paint`` omits that floor.
  * cold start = a fresh copy of the store file, a fresh launcher process, a fresh Chromium with an empty profile.
    The operating-system file cache is NOT flushed (that needs privileges); the copy is cache-resident.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

# Playwright finds its browsers under HOME; the benchmark points HOME at a disposable directory.
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path.home() / ("Library/Caches" if sys.platform == "darwin" else ".cache") / "ms-playwright")

import fixture as fx  # noqa: E402
from nexus_memory.domain.models import Scope  # noqa: E402

TARGETS = {"overview_ms": 2000, "warm_selection_p95_ms": 300}
INIT_SCRIPT = """
(() => {
  const B = window.__bench = { overview: null, fetches: [] };
  const original = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await original(...args);
    B.fetches.push({ path: new URL(String(args[0].href ?? args[0].url ?? args[0]), location.href).pathname, end: performance.now() });
    return response;
  };
  const watch = () => {
    const status = document.getElementById('status');
    if (!status) return false;
    const check = () => { if (B.overview === null && /active memories/.test(status.textContent)) { B.overview = performance.now(); performance.mark('overview-counted'); } };
    new MutationObserver(check).observe(status, { childList: true, characterData: true, subtree: true });
    check();
    return true;
  };
  if (!watch()) document.addEventListener('DOMContentLoaded', watch);
})();
"""
ACTION_JS = """
async (arg) => {
  const B = window.__bench;
  const frames = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const until = async (predicate) => {
    const limit = performance.now() + 15000;
    while (!predicate()) { if (performance.now() > limit) throw new Error('timeout waiting for ' + arg.action); await new Promise((r) => requestAnimationFrame(r)); }
  };
  const focusId = () => document.querySelector('.node.focus')?.dataset.nodeId ?? null;
  const hubItems = () => [...document.querySelectorAll('#graph-lists section')].find((s) => s.textContent.includes('citing memories of src/hub.py'));
  const hubTitle = () => hubItems()?.querySelector('h3')?.textContent ?? null;
  const prevFocus = focusId(), prevPage = document.getElementById('page').textContent, prevHub = hubTitle();
  const fetchedAfter = (path, t) => B.fetches.some((f) => f.path === path && f.end >= t);
  let button;
  if (arg.action === 'select') button = document.querySelector('#results li button');
  else if (arg.action === 'page') button = document.getElementById('next');
  else if (arg.action === 'hub-open') button = [...document.querySelectorAll('#graph-lists li')].find((li) => li.textContent.includes('src/hub.py')).querySelector('button');
  else if (arg.action === 'hub-next') button = [...hubItems().querySelectorAll('button')].find((b) => b.textContent === 'Next citing memories');
  else if (arg.action === 'hub-open-last') {
    const rows = [...hubItems().querySelectorAll('li')].filter((li) => !li.textContent.includes(arg.skip));
    button = rows[rows.length - 1].querySelector('button');
  } else if (arg.action === 'back') button = document.getElementById('back');
  const t0 = performance.now();
  button.click();
  if (arg.action === 'select' || arg.action === 'hub-open-last') {
    await until(() => focusId() !== prevFocus && fetchedAfter('/api/neighbors', t0) && document.getElementById('memory-title').textContent);
  } else if (arg.action === 'page') {
    await until(() => document.getElementById('page').textContent !== prevPage && document.querySelectorAll('#results li').length > 0);
  } else if (arg.action === 'hub-open') {
    await until(() => hubTitle() !== null);
  } else if (arg.action === 'hub-next') {
    await until(() => hubTitle() !== prevHub);
  } else if (arg.action === 'back') {
    await until(() => focusId() === arg.expect && document.getElementById('memory').hidden === false);
  }
  const ready = performance.now() - t0;
  await frames();
  const ms = performance.now() - t0;
  return { ms, ready, nodes: document.querySelectorAll('[data-node-id]').length, edges: document.querySelectorAll('.edge').length,
           focus: focusId(), hub: hubTitle() };
}
"""


# ------------------------------------------------------------------ statistics ---------------------------

def summarize(values: list[float]) -> dict:
    ordered = sorted(values)
    rank = lambda p: ordered[max(0, min(len(ordered) - 1, -(-int(p * len(ordered) * 1000) // 1000) - 1))]   # noqa: E731
    return {"n": len(ordered), "p50": round(statistics.median(ordered), 2), "p95": round(rank(0.95), 2),
            "max": round(ordered[-1], 2), "min": round(ordered[0], 2), "spread": round(ordered[-1] - ordered[0], 2),
            "stdev": round(statistics.pstdev(ordered), 2) if len(ordered) > 1 else 0.0}


# ------------------------------------------------------------------ subjects ---------------------------

def _git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "b", "GIT_AUTHOR_EMAIL": "b@example.invalid", "GIT_COMMITTER_NAME": "b",
           "GIT_COMMITTER_EMAIL": "b@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=env)


class Subject:
    """A disposable enrolled project whose store can be reset from a pristine copy."""

    def __init__(self, work: Path, name: str, integration_dir: Path | None = None) -> None:
        import importlib.util
        integration_dir = integration_dir or ROOT / "integrations" / "project_scope"
        self.home = work / name / "home"
        self.home.mkdir(parents=True, mode=0o700)
        os.environ["HOME"] = str(self.home)
        self.checkout = work / name / "repo"
        self.checkout.mkdir()
        _git(self.checkout, "init", "-q")
        _git(self.checkout, "commit", "-q", "--allow-empty", "-m", "init")
        self.checkout = self.checkout.resolve()
        spec = importlib.util.spec_from_file_location(f"bench_scope_{name}", integration_dir / "project_scope.py")
        self.scope_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.scope_module
        spec.loader.exec_module(self.scope_module)
        self.project_id = self.scope_module.enroll(self.checkout)
        self.database = self.scope_module.resolve(self.checkout).database
        self.scope = Scope(self.project_id, "local")
        self.pristine = work / name / "pristine.sqlite3"
        self.manifest: dict = {}

    def build(self, spec: fx.Spec, progress=print) -> None:
        self.manifest = fx.build_fixture(self.database, self.scope, spec, progress)
        with sqlite3.connect(self.database) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copyfile(self.database, self.pristine)

    def reset_store(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.database) + suffix).unlink(missing_ok=True)
        shutil.copyfile(self.pristine, self.database)
        os.chmod(self.database, 0o600)

    def env(self) -> dict:
        return {**os.environ, "HOME": str(self.home)}

    def resolve(self, workspace):
        return self.scope_module.resolve(Path(workspace))


class Viewer:
    """The actual viewer: ``launcher.py atlas`` in its own process, started from outside the source tree."""

    def __init__(self, subject: Subject, python: str, launcher: Path, cwd: Path) -> None:
        self.started = time.perf_counter()
        self.process = subprocess.Popen([python, "-I", str(launcher), "atlas", str(subject.checkout), "--no-browser"],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=subject.env(), cwd=cwd)
        self.address = self.launch_file = None
        for line in self.process.stdout:
            if line.startswith("Memory Atlas is running at "):
                self.address = line.rsplit(" ", 1)[1].strip()
            elif "Open this private file" in line:
                self.launch_file = Path(line.split(": ", 1)[1].strip())
                break
        self.process_launch_ms = (time.perf_counter() - self.started) * 1000
        if self.launch_file is None:
            raise RuntimeError("viewer did not start: " + (self.process.stderr.read() if self.process.poll() is not None else ""))

    def stop(self) -> None:
        import signal
        self.process.send_signal(signal.SIGINT)
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()


# ------------------------------------------------------------------ browser steps ---------------------------

def new_page(playwright, width: int = 1024, height: int = 800):
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script(INIT_SCRIPT)
    page = context.new_page()
    page.set_default_timeout(15000)
    return browser, page


def wait_overview(page, timeout: float = 15.0) -> float:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        value = page.evaluate("window.__bench && window.__bench.overview")
        if value is not None:
            return value
        time.sleep(0.002)
    raise TimeoutError("overview was never counted")


def cold_run(playwright, subject: Subject, python: str, launcher: Path, cwd: Path) -> dict:
    subject.reset_store()
    t = time.perf_counter()
    browser, page = new_page(playwright)
    browser_ms = (time.perf_counter() - t) * 1000
    viewer = Viewer(subject, python, launcher, cwd)
    try:
        t = time.perf_counter()
        page.goto(viewer.launch_file.as_uri())
        in_page = wait_overview(page)
        wall = (time.perf_counter() - t) * 1000
    finally:
        browser.close()
        viewer.stop()
    return {"browser_launch_ms": round(browser_ms, 1), "process_launch_ms": round(viewer.process_launch_ms, 1),
            "goto_to_counted_overview_wall_ms": round(wall, 1), "in_page_counted_overview_ms": round(in_page, 1),
            "launch_to_overview_ms": round(viewer.process_launch_ms + wall, 1)}


def noop_run(playwright) -> dict:
    """The same browser steps against a static page: Chromium and harness overhead, no Atlas."""
    page_html = (b"<!doctype html><meta charset=utf-8><title>noop</title><p id=status>Starting</p>"
                 b"<script>setTimeout(()=>{document.getElementById('status').textContent='10 active memories'},0)</script>")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):   # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(page_html)))
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page_html)

        def log_message(self, *a):
            pass

    t = time.perf_counter()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    process_ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    browser, page = new_page(playwright)
    browser_ms = (time.perf_counter() - t) * 1000
    try:
        t = time.perf_counter()
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
        in_page = wait_overview(page)
        wall = (time.perf_counter() - t) * 1000
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
    return {"browser_launch_ms": round(browser_ms, 1), "process_launch_ms": round(process_ms, 1),
            "goto_to_counted_overview_wall_ms": round(wall, 1), "in_page_counted_overview_ms": round(in_page, 1),
            "launch_to_overview_ms": round(process_ms + wall, 1)}


def cold_summary(runs: list[dict]) -> dict:
    return {key: summarize([r[key] for r in runs]) for key in runs[0]}


def search_for(page, text: str, expect: str, token: str | None = None) -> None:
    """Run a search and wait for ITS result: the count text alone can still be the previous search's."""
    page.fill("#q", text)
    page.get_by_role("button", name="Search", exact=True).click()
    end = time.perf_counter() + 15
    while time.perf_counter() < end:
        if expect in page.inner_text("#count") and (token is None or token in page.inner_text("#results")):
            return
        time.sleep(0.005)
    raise TimeoutError(f"search {text!r} never showed {expect!r}")


def act(page, **arg) -> dict:
    return page.evaluate(ACTION_JS, arg)


def pick_targets(manifest: dict, count: int) -> list[dict]:
    """Distinct targets stratified over hub citers, revised, isolated and ordinary memories."""
    order = ("hub", "revised", "isolated", "ordinary")
    per = [count // 4 + (1 if i < count % 4 else 0) for i in range(4)]
    chosen: list[dict] = []
    for label, take in zip(order, per):
        pool = manifest["strata"][label]
        stride = max(1, len(pool) // take)
        for i in range(take):
            chosen.append({**pool[(i * stride + 1) % len(pool)], "stratum": label})
    assert len({c["memory_id"] for c in chosen}) == count, "targets are not distinct"
    return chosen


def warm_selections(page, manifest: dict, count: int) -> dict:
    samples, maxima = [], {"nodes": 0, "edges": 0}
    for target in pick_targets(manifest, count):
        search_for(page, target["token"], "1 matching", target["token"])
        result = act(page, action="select")
        samples.append({"memory_id": target["memory_id"], "stratum": target["stratum"], "ms": round(result["ms"], 2),
                        "ready_ms": round(result["ready"], 2)})
        maxima["nodes"] = max(maxima["nodes"], result["nodes"])
        maxima["edges"] = max(maxima["edges"], result["edges"])
    by_stratum = {s: summarize([x["ms"] for x in samples if x["stratum"] == s]) for s in ("hub", "revised", "isolated", "ordinary")}
    return {"summary": summarize([s["ms"] for s in samples]),
            "summary_data_ready_before_paint": summarize([s["ready_ms"] for s in samples]), "by_stratum": by_stratum, "max_visible": maxima, "samples": samples}


def late_pages(page, pages: int) -> dict:
    search_for(page, "memory", " matching", "bm")
    end = time.perf_counter() + 15
    while page.is_disabled("#next") and time.perf_counter() < end:     # the previous search's text can still be showing
        time.sleep(0.005)
    samples = [round(act(page, action="page")["ms"], 2) for _ in range(pages)]
    return {"pages_advanced": pages, "summary": summarize(samples), "samples": samples}


def hub_journey(playwright, viewer: Viewer, manifest: dict, width: int) -> dict:
    browser, page = new_page(playwright, width=width, height=800)
    try:
        page.goto(viewer.launch_file.as_uri())
        wait_overview(page)
        target = manifest["strata"]["hub"][3]
        search_for(page, target["token"], "1 matching", target["token"])
        first = act(page, action="select")
        opened = act(page, action="hub-open")
        pages, maxima = [], {"nodes": opened["nodes"], "edges": opened["edges"]}
        while True:
            next_button = page.get_by_role("button", name="Next citing memories")
            if next_button.is_disabled():
                break
            step = act(page, action="hub-next")
            pages.append(round(step["ms"], 2))
            maxima["nodes"] = max(maxima["nodes"], step["nodes"])
            maxima["edges"] = max(maxima["edges"], step["edges"])
        last_title = page.locator("#graph-lists section", has_text="citing memories of src/hub.py").locator("h3").inner_text()
        chosen = act(page, action="hub-open-last", skip=target["token"])
        back = act(page, action="back", expect=first["focus"])
        for step in (chosen, back):
            maxima["nodes"] = max(maxima["nodes"], step["nodes"])
            maxima["edges"] = max(maxima["edges"], step["edges"])
        return {"width": width, "first_selection_ms": round(first["ms"], 2), "hub_open_ms": round(opened["ms"], 2),
                "hub_pages": len(pages) + 1, "hub_next_ms": summarize(pages) if pages else None, "last_title": last_title,
                "open_last_result_ms": round(chosen["ms"], 2), "back_ms": round(back["ms"], 2), "max_visible": maxima,
                "hub_next_samples": pages}
    finally:
        browser.close()


def binding_tiers(subject: Subject, repeat_full: int = 200, repeat_quick: int = 5000) -> dict:
    from nexus_memory.atlas.binding import open_binding
    os.environ["HOME"] = str(subject.home)
    binding = open_binding(subject.checkout, subject.resolve)

    def timed(fn, n):
        out = []
        for _ in range(n):
            t = time.perf_counter()
            fn()
            out.append((time.perf_counter() - t) * 1000)
        return summarize(out)

    return {"full_check_with_git_ms": timed(binding.full_check, repeat_full),
            "quick_check_stat_only_ms": timed(binding.check, repeat_quick),
            "reader_including_quick_check_ms": timed(binding.reader, repeat_quick)}


# ------------------------------------------------------------------ report ---------------------------

def hardware() -> dict:
    def run(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:   # noqa: BLE001
            return ""
    info = {"platform": platform.platform(), "python": platform.python_version(), "machine": platform.machine()}
    if sys.platform == "darwin":
        for line in run("system_profiler", "SPHardwareDataType").splitlines():
            key, _, value = line.strip().partition(": ")
            if key in {"Model Name", "Model Identifier", "Chip", "Total Number of Cores", "Memory"}:
                info[key.lower().replace(" ", "_")] = value
    return info


def git_commit() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memories", type=int, default=10_000)
    parser.add_argument("--interactions", type=int, default=100)
    parser.add_argument("--cold-runs", type=int, default=10)
    parser.add_argument("--late-pages", type=int, default=40)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--no-generate", action="store_true", help="check the fixture contract and stop: fails on an empty store")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--launcher", type=Path, default=ROOT / "integrations" / "project_scope" / "launcher.py")
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="atlas-bench-"))
    os.chmod(work, 0o700)
    os.umask(0o077)
    spec = fx.Spec(memories=args.memories, hub_citers=max(1, int(args.memories * 0.12)), hub_min=min(1000, int(args.memories * 0.1)),
                   revised=max(1, args.memories // 20), forgotten=max(1, args.memories // 200), foreign=max(1, args.memories * 3 // 100))
    subject = Subject(work, "main")
    if args.no_generate:
        problems = fx.check_fixture(subject.database, subject.scope, spec)
        print("fixture contract:", "MET" if not problems else "NOT MET")
        for problem in problems:
            print(" -", problem)
        return 0 if not problems else 2
    print(f"building fixture ({args.memories} memories) in {work}", flush=True)
    t = time.perf_counter()
    subject.build(spec, progress=lambda m: print(" ", m, flush=True))
    build_s = time.perf_counter() - t
    problems = fx.check_fixture(subject.database, subject.scope, spec)
    if problems:
        print("fixture contract NOT MET:", *problems, sep="\n - ")
        return 2
    print(f"fixture contract met ({build_s:.1f} s to build)", flush=True)

    from playwright.sync_api import sync_playwright
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_commit": git_commit(),
                    "hardware": hardware(), "targets": TARGETS, "fixture": {**subject.manifest["counts"], "build_seconds": round(build_s, 1),
                    "database_bytes": subject.database.stat().st_size, "contract_problems": problems},
                    "definitions": __doc__, "interactions_requested": args.interactions}
    cwd = work  # outside the source tree
    with sync_playwright() as playwright:
        probe = playwright.chromium.launch()
        report["hardware"]["chromium"] = probe.version
        probe.close()
        from importlib.metadata import version
        report["hardware"]["playwright"] = version("playwright")

        print("controls: static no-op page", flush=True)
        noop = [noop_run(playwright) for _ in range(args.cold_runs)]
        print("controls: tiny store", flush=True)
        tiny = Subject(work, "tiny")
        tiny.build(fx.Spec(memories=10, hub_citers=3, hub_min=2, revised=1, forgotten=1, foreign=2), progress=lambda m: None)
        tiny_runs = [cold_run(playwright, tiny, args.python, args.launcher, cwd) for _ in range(args.cold_runs)]
        os.environ["HOME"] = str(subject.home)
        print("cold start x", args.cold_runs, flush=True)
        cold = [cold_run(playwright, subject, args.python, args.launcher, cwd) for _ in range(args.cold_runs)]
        report["controls"] = {"static_noop_page": {"runs": noop, "summary": cold_summary(noop)},
                              "tiny_store_10_memories": {"runs": tiny_runs, "summary": cold_summary(tiny_runs)}}
        report["cold_start"] = {"cache_state": "fresh store copy per run; fresh launcher process; fresh Chromium profile; OS file cache not flushed",
                                "runs": cold, "summary": cold_summary(cold)}

        subject.reset_store()
        viewer = Viewer(subject, args.python, args.launcher, cwd)
        try:
            browser, page = new_page(playwright)
            page.goto(viewer.launch_file.as_uri())
            wait_overview(page)
            print("warm selections", flush=True)
            warm = warm_selections(page, subject.manifest, args.interactions)
            print("late result pages", flush=True)
            late = late_pages(page, args.late_pages)
            browser.close()
            report["warm_selection"] = warm
            report["late_result_pages"] = late
            hubs = {}
            for width in (1024, 360):
                print("hub journey at", width, flush=True)
                # the launch secret is single-use: a new viewer per browser context
                viewer.stop()
                subject.reset_store()
                viewer = Viewer(subject, args.python, args.launcher, cwd)
                hubs[str(width)] = hub_journey(playwright, viewer, subject.manifest, width)
            report["hub_journey"] = hubs
        finally:
            viewer.stop()
    print("binding tiers", flush=True)
    report["binding_tiers"] = binding_tiers(subject)
    store_after = [p for p in (subject.database,)]
    report["raw_database_files"] = [str(p) for p in store_after]

    # measured against target, kept apart
    overview_p95 = report["cold_start"]["summary"]["launch_to_overview_ms"]["p95"]
    selection_p95 = report["warm_selection"]["summary"]["p95"]
    report["verdict_vs_targets"] = {
        "launch_to_counted_overview_p95_ms": {"measured": overview_p95, "target": TARGETS["overview_ms"], "met": overview_p95 <= TARGETS["overview_ms"]},
        "warm_selection_p95_ms": {"measured": selection_p95, "target": TARGETS["warm_selection_p95_ms"], "met": selection_p95 <= TARGETS["warm_selection_p95_ms"]},
    }
    out = args.out or (work / "report.json")
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps({k: report[k] for k in ("verdict_vs_targets", "binding_tiers")}, indent=2))
    print("report:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
