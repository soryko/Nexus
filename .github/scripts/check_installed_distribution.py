#!/usr/bin/env python3
"""Does the installed distribution still work once the source that built it is gone?

Every check this project had before this one ran against a source installation, where
`uv sync` links the environment back to the checkout. Such an environment passes the whole
nine-step lifecycle while remaining completely dependent on a directory the user is free
to delete -- so a green lifecycle check has never been evidence for the claim that the
installation is independent, and reading it as such is the specific mistake this script
exists to prevent.

So this builds the real thing and then takes the source away:

  1. ARCHIVE    `git archive HEAD` into a disposable copy -- the caller's checkout is
                never moved, written to or deleted
  2. INSTALL    run that copy's `tools/install.py` into a separate environment
  3. ORIGIN     confirm the package lives in the environment and is NOT editable
  4. SEED       store a memory through the installed package's own MCP client helper and
                keep the real receipt
  5. REMOVE     rename the disposable source away and confirm the path is gone
  6. LIFECYCLE  run `nexus-memory-check` as a fresh process: nine checks against a NEW
                database, which is also what exercises the packaged SQL migrations
  7. RETRIEVE   a fresh server on the seeded database: same bytes, same ids, search still
                finds it, and the whole receipt replays without a duplicate
  8. RE-ORIGIN  probe the package again in a new interpreter, so no parent process's
                import cache can stand in for an installed module

Paths deliberately contain spaces. Subprocesses are run from outside every checkout with
`PYTHONPATH` and `PYTHONHOME` stripped, because an inherited path entry would hide exactly
the dependency under test.

`--editable-control` runs the OLD mechanism through the same removal instead, and requires
it to pass before removal and fail after. Without that, a passing candidate says only that
this script's removal step is detectable in principle -- not that it detects anything.

    python3 .github/scripts/check_installed_distribution.py \\
        --repo . --python "$(uv python find --system 3.13.14)" --artifacts artifacts

Exit 0 only if every step held. The report is written whatever happens; success is never
inferred from a missing report.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Spaces on purpose: a quoting defect in the installer, the console scripts or this
# driver should fail here rather than in a user's home directory.
SOURCE_DIR = "nexus source copy"
SOURCE_GONE = "nexus source copy (removed)"
ENV_DIR = "nexus runtime env"

TIMEOUT = 900


class Failed(RuntimeError):
    """A step did not hold. Carries what was run, so the report can say so."""


class Driver:
    def __init__(self, repo: Path, python: Path, artifacts: Path, work: Path) -> None:
        self.repo = repo
        self.python = python
        self.artifacts = artifacts
        self.work = work
        self.source = work / SOURCE_DIR
        self.env = work / ENV_DIR
        self.steps: list[dict] = []

    # ---------------------------------------------------------------- plumbing

    @property
    def bin(self) -> Path:
        return self.env / ("Scripts" if os.name == "nt" else "bin")

    @property
    def env_python(self) -> Path:
        return self.bin / ("python.exe" if os.name == "nt" else "python")

    def _clean_env(self) -> dict:
        """The caller's environment minus anything that could substitute for an install."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONHOME", "UV_PYTHON")}
        env["UV_PROJECT_ENVIRONMENT"] = str(self.env)
        return env

    def run(self, cmd: list[str], *, cwd: Path | None = None,
            expect: int | None = 0) -> subprocess.CompletedProcess:
        """Run from OUTSIDE every checkout by default, with the source paths stripped."""
        proc = subprocess.run(
            [str(c) for c in cmd], capture_output=True, text=True, timeout=TIMEOUT,
            cwd=str(cwd or self.work), env=self._clean_env(),
        )
        if expect is not None and proc.returncode != expect:
            raise Failed(
                f"{' '.join(str(c) for c in cmd)}\n"
                f"  expected exit {expect}, got {proc.returncode}\n"
                f"  stdout: {proc.stdout[-2000:]}\n  stderr: {proc.stderr[-2000:]}")
        return proc

    def python_snippet(self, code: str, *args: str, expect: int | None = 0) -> dict:
        """Run `code` in a FRESH interpreter from the installed environment.

        A new process every time and never an import in this one: a module already
        imported here would answer for a package that is no longer on disk.
        """
        proc = self.run([self.env_python, "-c", code, *args], expect=expect)
        if expect != 0:
            return {"returncode": proc.returncode, "stderr": proc.stderr[-2000:]}
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise Failed(f"expected JSON from the installed interpreter, got:\n"
                         f"  stdout: {proc.stdout[-2000:]}\n  ({exc})") from None

    def step(self, name: str, detail: str, **extra) -> None:
        self.steps.append({"step": name, "passed": True, "detail": detail, **extra})
        print(f"  ok  {name:<38} {detail}", flush=True)

    # ------------------------------------------------------------------- steps

    def archive(self) -> str:
        """A disposable copy via `git archive`: the caller's checkout is only read."""
        self.source.mkdir(parents=True)
        sha = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        tar = self.work / "source.tar"
        with tar.open("wb") as fh:
            subprocess.run(["git", "-C", str(self.repo), "archive", "HEAD"],
                           stdout=fh, check=True, timeout=TIMEOUT)
        shutil.unpack_archive(str(tar), str(self.source), format="tar")
        tar.unlink()
        if not (self.source / "tools" / "install.py").is_file():
            raise Failed(f"the archive of {sha} has no tools/install.py")
        self.step("disposable source copy", f"{sha[:12]} -> {self.source}")
        return sha

    def install(self) -> None:
        proc = self.run([self.python, self.source / "tools" / "install.py",
                         "--python", self.python, "--venv", self.env],
                        cwd=self.source)
        (self.artifacts / "install.log").write_text(proc.stdout + proc.stderr)
        for required in (self.bin / "nexus-memory", self.bin / "nexus-memory-check"):
            if not required.exists():
                raise Failed(f"the installer reported success without {required}")
        # The installer must point users at a command that outlives the checkout.
        if "tools/check_install.py" in proc.stdout:
            raise Failed("the installer still prints a verification command that needs "
                         "the source checkout")
        self.step("installer built the environment", str(self.env))

    def origin(self, label: str) -> dict:
        """Where the package's code actually is, and whether it is editable."""
        info = self.python_snippet(
            "import json,sys,importlib.util;"
            "import importlib.metadata as md;"
            "import nexus_memory.install_check as ic;"
            "import nexus_memory.storage.sqlite as sq;"
            "d=md.distribution('nexus-memory');"
            "raw=d.read_text('direct_url.json');"
            "print(json.dumps({"
            "'install_check': ic.__file__, 'sqlite_module': sq.__file__,"
            "'prefix': sys.prefix, 'version': md.version('nexus-memory'),"
            "'direct_url': json.loads(raw) if raw else None,"
            "'pytest_present': importlib.util.find_spec('pytest') is not None}))")

        in_env = info["install_check"].startswith(info["prefix"])
        editable = bool((info.get("direct_url") or {}).get("dir_info", {}).get("editable"))
        if not in_env:
            raise Failed(f"[{label}] the package is imported from outside the environment: "
                         f"{info['install_check']}")
        if editable:
            raise Failed(f"[{label}] the installation is editable; it still points at a "
                         f"source tree: {info.get('direct_url')}")
        if str(self.source) in info["install_check"]:
            raise Failed(f"[{label}] the package is imported from the source copy: "
                         f"{info['install_check']}")
        if info["pytest_present"]:
            raise Failed(f"[{label}] pytest is present in a user runtime environment")
        self.step(f"package origin ({label})",
                  f"{info['version']} in site-packages, editable=False, no dev deps")
        return info

    def seed(self, db: Path) -> dict:
        """Store one memory through the installed package's OWN MCP client helper.

        Not a fabricated record: the same `_attempt` the checker uses, so what is read
        back after removal was written by the code path under test.
        """
        seeded = self.python_snippet(
            "import json,sys;"
            "from nexus_memory.install_check import CONTENT, KIND, TAGS, _attempt;"
            "server, db = sys.argv[1:3];"
            "r=_attempt(server, db, 'distribution-check', 'local',"
            " [('record', {'content': CONTENT, 'kind': KIND, 'tags': TAGS,"
            "   'idempotency_key': 'distribution-check-1'})])[0];"
            "print(json.dumps({'receipt': r, 'content': CONTENT,"
            " 'kind': KIND, 'tags': TAGS}))",
            str(self.bin / "nexus-memory"), str(db))
        receipt = seeded["receipt"]
        missing = [f for f in ("memory_id", "revision_id", "operation_id", "durable_seq",
                               "operation") if receipt.get(f) is None]
        if missing:
            raise Failed(f"the seed receipt is missing {missing}; comparing it after "
                         f"removal would pass vacuously: {receipt}")
        (self.artifacts / "seed.json").write_text(json.dumps(seeded, indent=1) + "\n")
        self.step("seeded a memory before removal",
                  f"memory_id={receipt['memory_id'][:12]}… durable_seq="
                  f"{receipt['durable_seq']}")
        return seeded

    def remove_source(self) -> None:
        """Rename the source away. Not a chdir: a chdir removes nothing."""
        gone = self.work / SOURCE_GONE
        self.source.rename(gone)
        if self.source.exists():
            raise Failed(f"{self.source} still exists after the rename")
        shutil.rmtree(gone, ignore_errors=True)
        if self.source.exists() or gone.exists():
            raise Failed("the source copy could not be removed")
        self.step("source removed", f"{self.source} no longer exists")

    def lifecycle(self) -> dict:
        """The nine checks, as a fresh process, against a database that does not exist yet.

        A new database means the schema is built from the packaged SQL, so this covers the
        migration resources as well as the Python modules.
        """
        report = self.artifacts / "install-check.json"
        proc = self.run([self.bin / "nexus-memory-check", "--json", report])
        (self.artifacts / "lifecycle.log").write_text(proc.stdout + proc.stderr)
        data = json.loads(report.read_text())
        if not data.get("passed"):
            raise Failed(f"the lifecycle check failed after removal:\n{proc.stdout[-2000:]}")
        if len(data.get("steps", [])) != 9:
            raise Failed(f"expected 9 checks, the report has {len(data.get('steps', []))}; "
                         f"a shortened check is not a passing one")
        self.step("lifecycle after removal",
                  f"{len(data['steps'])}/{len(data['steps'])} checks, new database")
        return data

    def retrieve(self, db: Path, seeded: dict) -> dict:
        """The memory stored BEFORE removal, read by a server started after it."""
        got = self.python_snippet(
            "import json,sys;"
            "from nexus_memory.install_check import CONTENT, _attempt;"
            "server, db = sys.argv[1:3];"
            "out=_attempt(server, db, 'distribution-check', 'local',"
            " [('get', {'memory_id': sys.argv[3]}),"
            "  ('search', {'query': 'backoff', 'limit': 10}),"
            "  ('record', {'content': CONTENT, 'kind': 'procedure',"
            "    'tags': ['payments','install-check'],"
            "    'idempotency_key': 'distribution-check-1'}),"
            "  ('status', {})]);"
            "print(json.dumps({'got': out[0], 'search': out[1],"
            " 'replay': out[2], 'status': out[3]}))",
            str(self.bin / "nexus-memory"), str(db), seeded["receipt"]["memory_id"])

        receipt, mem_id = seeded["receipt"], seeded["receipt"]["memory_id"]
        if got["got"].get("content", "").encode() != seeded["content"].encode():
            raise Failed("the content read back differs from the bytes stored before "
                         "removal")
        if got["got"].get("revision_id") != receipt["revision_id"]:
            raise Failed("the revision id changed across removal")
        if not any(h.get("memory_id") == mem_id for h in got["search"].get("hits") or []):
            raise Failed("search no longer finds the memory stored before removal")
        differing = [f for f in ("memory_id", "revision_id", "operation_id",
                                 "durable_seq", "operation")
                     if got["replay"].get(f) != receipt.get(f)]
        if differing:
            raise Failed(f"the retry did not replay the whole receipt: {differing}")
        if got["status"].get("active_memories") != 1:
            raise Failed(f"the retry duplicated the memory: "
                         f"active_memories={got['status'].get('active_memories')}")
        self.step("pre-removal memory retrieved",
                  f"{len(seeded['content'].encode())} bytes identical, ids stable, "
                  f"receipt replayed, active_memories=1")
        return got

    # ------------------------------------------------------------------ drives

    def candidate(self) -> dict:
        sha = self.archive()
        self.install()
        before = self.origin("source present")
        db = self.work / "seeded store" / "memory.sqlite3"
        db.parent.mkdir(parents=True)
        self.run([self.bin / "nexus-memory-check", "--json",
                  self.artifacts / "install-check-before.json"])
        self.step("lifecycle before removal", "9/9, establishes the baseline")
        seeded = self.seed(db)
        self.remove_source()
        lifecycle = self.lifecycle()
        retrieved = self.retrieve(db, seeded)
        after = self.origin("source removed")
        return {
            "candidate_sha": sha,
            "source_path": str(self.source),
            "source_absent_after_removal": not self.source.exists(),
            "environment": str(self.env),
            "origin_before": before,
            "origin_after": after,
            "database": str(db),
            "lifecycle_after_removal": {
                "passed": lifecycle["passed"],
                "checks": len(lifecycle["steps"]),
                "steps": [s["step"] for s in lifecycle["steps"]],
            },
            "persisted_memory": {
                "seed_receipt": seeded["receipt"],
                "content_bytes": len(seeded["content"].encode()),
                "content_identical": (retrieved["got"].get("content", "").encode()
                                      == seeded["content"].encode()),
                "revision_stable": (retrieved["got"].get("revision_id")
                                    == seeded["receipt"]["revision_id"]),
                "search_hits": len(retrieved["search"].get("hits") or []),
                "replay_receipt": retrieved["replay"],
                "active_memories": retrieved["status"].get("active_memories"),
            },
        }

    def control(self) -> dict:
        """The OLD mechanism, through the same removal. It must fail, and only after.

        A control that fails before the source is hidden has established nothing about
        source removal -- it has found a broken environment.
        """
        sha = self.archive()
        self.run(["uv", "venv", "--python", self.python, self.env], cwd=self.source)
        self.run(["uv", "sync", "--frozen"], cwd=self.source)
        checker = self.bin / "nexus-memory-check"
        if not checker.exists():
            raise Failed("the editable environment has no checker to control with")

        before = self.run([checker, "--json", self.artifacts / "control-before.json"],
                          expect=None)
        if before.returncode != 0:
            raise Failed(f"the editable control failed BEFORE removal (exit "
                         f"{before.returncode}); it cannot demonstrate anything about "
                         f"removal:\n{before.stdout[-2000:]}")
        self.step("editable control passes with source", "9/9 while the checkout exists")

        self.remove_source()
        after = self.run([checker, "--json", self.artifacts / "control-after.json"],
                         expect=None)
        if after.returncode == 0:
            raise Failed("the editable control PASSED after its source was removed, so "
                         "this driver's removal step does not detect the dependency it "
                         "was written to detect")
        self.step("editable control fails without source",
                  f"exit {after.returncode}: {(after.stderr or after.stdout).strip().splitlines()[-1][:90]}")
        return {"candidate_sha": sha, "before_exit": before.returncode,
                "after_exit": after.returncode,
                "after_stderr": after.stderr[-2000:]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify the installed distribution outlives its source checkout.")
    ap.add_argument("--repo", default=".", type=Path)
    ap.add_argument("--python", required=True, type=Path)
    ap.add_argument("--artifacts", default="artifacts", type=Path)
    ap.add_argument("--editable-control", action="store_true",
                    help="run the OLD editable mechanism and require it to fail "
                         "only after removal")
    a = ap.parse_args(argv)

    repo = a.repo.resolve()
    artifacts = a.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    name = "editable-control" if a.editable_control else "distribution-check"
    report_path = artifacts / f"{name}.json"

    report: dict = {
        "mode": name,
        "started": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo),
        "requested_python": str(a.python),
        "host": {"platform": platform.platform(), "machine": platform.machine(),
                 "driver_python": sys.version.split()[0]},
        "passed": False,
    }

    print(f"Nexus installed-distribution check -- {name}\n"
          f"  repo       {repo}\n  python     {a.python}\n", flush=True)

    # Beside the artifacts, never inside either checkout.
    work = Path(tempfile.mkdtemp(prefix="nexus-dist-", dir=artifacts.parent))
    driver = Driver(repo, a.python, artifacts, work)
    try:
        if shutil.which("uv") is None:
            raise Failed("uv is not on PATH; this driver builds with the installer")
        report.update(driver.control() if a.editable_control else driver.candidate())
        report["passed"] = True
    except Failed as exc:
        report["failure"] = str(exc)
        print(f"\n  BAD {exc}", flush=True)
    except Exception as exc:                       # noqa: BLE001 - recorded, not swallowed
        report["failure"] = f"{exc.__class__.__name__}: {exc}"
        print(f"\n  BAD {exc.__class__.__name__}: {exc}", flush=True)
    finally:
        report["steps"] = driver.steps
        report["finished"] = datetime.now(timezone.utc).isoformat()
        report_path.write_text(json.dumps(report, indent=1) + "\n")
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{'PASS' if report['passed'] else 'FAIL'}: {len(driver.steps)} step(s)\n"
          f"-> {report_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
