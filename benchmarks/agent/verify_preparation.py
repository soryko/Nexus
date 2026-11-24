"""The four preparation items, each checked by executing it rather than by reading it.

No model is invoked and nothing is paid for. Every check here either runs against saved
traces, against synthetic ones, or against an instrument deliberately broken for the purpose.

  scheduling            the same inputs give the same schedule, different seeds give a
                        different one, and the orders vary BETWEEN pairs -- the property the
                        per-run reseeding lacked while still recording a seed.
  injected failures     three ways for the scorer to break, each of which must produce
                        `instrument_error` and an UNKNOWN verdict, and must leave every other
                        check in the record reportable.
  verdict round-trip    a scored record survives JSON with all four states intact, and its
                        counts still agree with its own checks after reloading.
  arm-run isolation     two attempts do not share an output path, an arm-run is given a
                        COPY of the corpus rather than the corpus, a write to that copy cannot
                        reach the master or the next arm-run, and the profile withholds the
                        master from all three arms.
  configuration         a valid config passes preflight, each kind of broken path is named
                        rather than raising at the point of use, and the child environment
                        carries the declared names and nothing else -- then the runner is
                        actually started under it, against a loopback stub, because cutting
                        60 host names to 8 is the change least likely to be caught by
                        reading it.

Usage:  <click-venv>/bin/python verify_preparation.py [<click-venv>/bin/python]

It is a script, like `test_compliance_counterexamples.py`, and for the same reason: it needs
an interpreter argument and it is not collected by the Nexus suite, whose `testpaths` is
`tests`. Sections 1, 3 and 4 run under any interpreter; section 2 needs one with a working
`pyexpat` only for its final case, which runs the real probe.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402
import isolation                                                        # noqa: E402
import schedule as sched                                                # noqa: E402
import score_compliance as SC                                           # noqa: E402

PY_CLICK = sys.argv[1] if len(sys.argv) > 1 else sys.executable
OK = True


def check(label: str, got, want) -> None:
    global OK
    ok = got == want
    OK &= ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r} (want {want!r})")


def note(text: str) -> None:
    print(f"        {text}")


# ---------------------------------------------------------------- 1. scheduling --------
def verify_scheduling() -> None:
    print("\n1. scheduling -- one generator across task-attempt pairs")
    tasks, attempts, seed = ["h1", "h2", "h3", "h4", "h5", "h6"], 3, 20260912

    a = sched.build(tasks, attempts, seed)
    b = sched.build(tasks, attempts, seed)
    check("the same (seed, tasks, attempts) reproduces the schedule", a == b, True)
    check("a different seed gives a different schedule",
          sched.build(tasks, attempts, seed + 1) != a, True)
    check("one row per task-attempt pair", len(a), len(tasks) * attempts)

    cb = sched.counterbalance(a)
    check("every arm appears exactly once per pair", cb["every_arm_appears_once_per_pair"], True)
    check("orders vary between pairs (the old harness had 1)",
          cb["distinct_orders"] > 1, True)
    note(f"{cb['distinct_orders']} distinct orders over {cb['pairs']} pairs; "
         f"ran first {cb['times_each_arm_ran_first']}")

    # the defect this replaces, reproduced: a fresh generator per task yields one order
    import random
    per_task = [(lambda o: (random.Random(seed).shuffle(o), o)[1])(list(sched.ARMS))
                for _ in tasks for _ in range(attempts)]
    check("reseeding per run would give exactly one order",
          len({tuple(o) for o in per_task}), 1)

    with tempfile.TemporaryDirectory() as td:
        s = sched.Schedule.create(tasks, attempts, seed)
        path = sched.save(s, Path(td) / "schedule.json")
        reloaded = sched.load(path)
        check("a saved schedule reloads with identical rows", reloaded.rows, s.rows)
        check("order_for reads the frozen row rather than re-deriving",
              reloaded.order_for("h4", 2), s.rows[10]["order"])
        try:
            reloaded.order_for("h9", 1)
            check("an unscheduled pair is refused", "no error", "KeyError")
        except KeyError:
            check("an unscheduled pair is refused", "KeyError", "KeyError")
        # tampering must be detected
        d = json.loads(path.read_text())
        d["rows"][0]["order"] = list(reversed(d["rows"][0]["order"]))
        path.write_text(json.dumps(d))
        try:
            sched.load(path)
            check("an edited schedule is rejected", "accepted", "rejected")
        except SystemExit:
            check("an edited schedule is rejected", "rejected", "rejected")


# ------------------------------------------------- 2. injected instrument failures -----
def _trace(*blocks) -> str:
    return "\n".join(json.dumps(b) for b in blocks)


TRACE = _trace(
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "mcp__nexus__search", "input": {"query": "x"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": False,
         "content": [{"type": "text", "text": json.dumps({"hits": [{"memory_id": "c1"}]})}]}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t2", "name": "Edit",
         "input": {"file_path": "/x/src/click/core.py"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t2", "is_error": False,
         "content": [{"type": "text", "text": "ok"}]}]}},
    {"type": "result", "is_error": False, "subtype": "success",
     "terminal_reason": "completed", "result": "DONE"})

PATCH = """diff --git a/src/click/core.py b/src/click/core.py
--- a/src/click/core.py
+++ b/src/click/core.py
@@ -1,3 +1,4 @@
 from __future__ import annotations
+# change

 import enum
diff --git a/tests/test_options.py b/tests/test_options.py
--- a/tests/test_options.py
+++ b/tests/test_options.py
@@ -1,3 +1,6 @@
 import os

+
+def test_added():
+    assert True
"""


def _scored(run: Path, pristine: Path, python: str) -> dict:
    (run / "arms" / "nexus").mkdir(parents=True, exist_ok=True)
    (run / "arms" / "nexus" / "patch.diff").write_text(PATCH)
    (run / "arms" / "nexus" / "trace.jsonl").write_text(TRACE)
    return SC.score(run, "d1", "nexus", pristine, python)


def verify_injected_failures() -> None:
    print("\n2. injected scorer failures -- instrument_error, not a failing arm")
    real_pytest, real_parse = SC._pytest, SC.ET.parse
    with tempfile.TemporaryDirectory() as td:
        run, pristine = Path(td) / "run", Path(td) / "pristine"
        (pristine / "src" / "click").mkdir(parents=True)
        (pristine / "tests").mkdir()
        (pristine / "src" / "click" / "core.py").write_text(
            "from __future__ import annotations\n\nimport enum\n")
        (pristine / "tests" / "test_options.py").write_text("import os\n\n")

        cases = {
            "a broken pyexpat (ImportError out of ET.parse)":
                lambda: setattr(SC.ET, "parse", _raise(ImportError("No module named expat"))),
            "an interpreter that cannot be launched":
                lambda: setattr(SC, "_pytest", _forward(real_pytest, python="/no/such/python")),
            "an unexpected fault anywhere in the probe":
                lambda: setattr(SC, "regression_discriminates",
                                _raise(RuntimeError("probe exploded"))),
        }
        real_regression = SC.regression_discriminates
        for label, inject in cases.items():
            SC._pytest, SC.ET.parse = real_pytest, real_parse
            SC.regression_discriminates = real_regression
            inject()
            r = _scored(run, pristine, PY_CLICK)
            e1 = r["checks"]["E1_regression_discriminates"]
            print(f"    -- {label}")
            check("E1 is unknown, not fail", e1["verdict"], SC.UNKNOWN)
            check("diagnostics are retained",
                  bool(e1.get("instrument_error", {}).get("detail")), True)
            note(f"stage={e1.get('instrument_error', {}).get('stage')} "
                 f"detail={e1.get('instrument_error', {}).get('detail', '')[:70]}")
            check("the instrument error is listed",
                  r["instrument_errors"], ["E1_regression_discriminates"])
            check("other checks still report",
                  sorted({v["verdict"] for k, v in r["checks"].items() if k != "E1"}) != [],
                  True)
            check("P1 still settled", r["checks"]["P1_final_reply_opens_done"]["verdict"],
                  SC.PASS)
            check("P2 still settled",
                  r["checks"]["P2_content_delivered_before_edit"]["verdict"], SC.PASS)
            check("the unknown is outside the ratio", r["compliance"].endswith("/5"), True)
            check("unknown_count counts it", r["unknown_count"], 1)
        SC._pytest, SC.ET.parse = real_pytest, real_parse
        SC.regression_discriminates = real_regression


def _raise(exc):
    def boom(*a, **k):
        raise exc
    return boom


def _forward(fn, **override):
    def wrapper(python, work, targets):
        return fn(override.get("python", python), work, targets)
    return wrapper


# -------------------------------------------------------- 3. verdict round-tripping ----
def verify_round_trip() -> None:
    print("\n3. verdict round-tripping -- all four states survive JSON")
    checks = {
        "A_pass": SC.verdict(SC.PASS, "it held"),
        "B_fail": SC.verdict(SC.FAIL, "it did not hold"),
        "C_unknown": SC.verdict(SC.UNKNOWN, "the trace cannot settle it"),
        "D_instrument": SC.verdict(SC.UNKNOWN, "the probe did not run",
                                   instrument_error=SC._instrument_error("junit_read", "boom")),
        "E_na": SC.verdict(SC.NA, "this arm is offered no memory"),
    }
    counts = SC.tally(checks)
    record = {"scorer_version": SC.SCORER_VERSION, "checks": checks, **counts}
    back = json.loads(json.dumps(record))

    check("every check keeps its verdict",
          [back["checks"][k]["verdict"] for k in sorted(back["checks"])],
          [SC.PASS, SC.FAIL, SC.UNKNOWN, SC.UNKNOWN, SC.NA])
    check("every check keeps a reason",
          all(back["checks"][k].get("reason") for k in back["checks"]), True)
    check("diagnostics survive", back["checks"]["D_instrument"]["instrument_error"]["stage"],
          "junit_read")
    check("the scorer version travels with them", back["scorer_version"], SC.SCORER_VERSION)
    check("compliance counts only settled checks", back["compliance"], "1/2")
    check("unknown_count is beside it", back["unknown_count"], 2)
    check("not_applicable_count is beside it", back["not_applicable_count"], 1)
    check("counts still agree with the checks after reloading",
          SC.tally(back["checks"]), counts)
    check("a saved record cannot be mistaken for 1/5",
          (back["compliance"], back["unknown_count"], back["not_applicable_count"]),
          ("1/2", 2, 1))


# ------------------------------------------------------- 4. configuration preflight ----
def verify_configuration() -> None:
    print("\n4. configuration preflight and the child environment")
    cfg = a1_config.load()
    check("the committed configuration is valid", cfg.preflight(), [])

    import dataclasses
    with tempfile.TemporaryDirectory() as td:
        missing = dataclasses.replace(cfg, source_clone=f"{td}/gone")
        check("a missing path is named", [p.split(":")[0] for p in missing.preflight()],
              ["source_clone"])
        notdir = dataclasses.replace(cfg, source_clone=cfg.pytest_python)
        check("a file where a directory is required is named",
              [p.split(":")[0] for p in notdir.preflight()], ["source_clone"])
        plain = Path(td) / "not-executable"
        plain.write_text("x")
        noexec = dataclasses.replace(cfg, pytest_python=str(plain))
        check("a non-executable interpreter is named",
              [p.split(":")[0] for p in noexec.preflight()], ["pytest_python"])
        notrepo = Path(td) / "bare"
        notrepo.mkdir()
        check("a source clone without .git is named",
              [p.split(":")[0] for p in dataclasses.replace(
                  cfg, source_clone=str(notrepo)).preflight()], ["source_clone"])
        bad = dataclasses.replace(cfg, forwarder_port=0, max_turns=0, corpus_size=-1,
                                  memory_probe_query="  ")
        # port, corpus_size, max_turns, memory_probe_query -- four fields, one pass
        check("every bad value is reported in one pass, not just the first",
              len(bad.preflight()), 4)
        check("require() refuses to proceed",
              _raises_systemexit(lambda: bad.require()), True)

    env = a1_config.child_env("http://127.0.0.1:8899/anthropic", "not-a-key")
    check("the child environment holds only declared names",
          sorted(set(env) - set(a1_config.ENV_ALLOWLIST) - set(a1_config.ENV_HARNESS_SET)), [])
    check("no secret name crosses by inheritance",
          [k for k in a1_config.ENV_SECRET if k in env], [])
    check("the harness sets the base URL itself",
          env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8899/anthropic")
    rec = a1_config.env_record(env)
    check("the record names what crossed, never a value",
          all(isinstance(v, (list, int)) for v in rec.values()), True)
    check("the key's VALUE is not in the record",
          "not-a-key" not in json.dumps(rec), True)
    note(f"{len(env)} names given to an arm; {len(__import__('os').environ)} on this host")


def verify_minimal_env_live() -> None:
    """The runner actually starts under the stripped environment, for free.

    Cutting 60 host names to 8 is the change most likely to break a run in a way no static
    check sees -- and the last two boundary defects were exactly that shape. So the runner is
    started for real, with `child_env` and nothing else, against a loopback stub standing in
    for the provider. No paid call, and the full startup path runs.
    """
    print("\n5. the minimal child environment, exercised (stub upstream, no paid call)")
    import http.server, os, socket, socketserver, subprocess, threading, time     # noqa: E401
    import isolation

    class Stub(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        seen: list[str] = []

        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0) or 0))
            Stub.seen.append(self.path)
            msg = json.dumps({"id": "m", "type": "message", "role": "assistant", "model": "x",
                              "content": [{"type": "text", "text": "hi"}],
                              "stop_reason": "end_turn",
                              "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

        def log_message(self, *a):
            pass

    cfg = a1_config.load()
    stub_port, fwd_port = 8904, 8905
    srv = socketserver.TCPServer(("127.0.0.1", stub_port), Stub)
    srv.allow_reuse_address = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fwd = subprocess.Popen(
        [sys.executable, str(BENCH / "model_forwarder.py"), str(fwd_port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=dict(os.environ, A1_FORWARDER_STUB_UPSTREAM=f"127.0.0.1:{stub_port}"))
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", fwd_port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / "README.md").write_text("fixture\n")
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            prof = isolation.write_profile(
                Path(td) / "p.sb", repo, [],
                isolation.default_allow_paths(repo, cfg.pytest_python) + [repo], fwd_port)
            env = a1_config.child_env(f"http://127.0.0.1:{fwd_port}/anthropic", "stub-not-a-key")
            r = subprocess.run(
                ["sandbox-exec", "-f", str(prof), "claude", "--bare", "-p", "say hi",
                 "--model", "deepseek-flash", "--strict-mcp-config",
                 "--disable-slash-commands", "--max-turns", "2",
                 "--output-format", "stream-json", "--verbose"],
                cwd=repo, env=env, capture_output=True, text=True, timeout=120)
            started = '"type":"system"' in (r.stdout or "") and '"subtype":"init"' in (r.stdout or "")
            check("the runner starts under the minimal environment", started, True)
            # the runner appends its own query string (`?beta=true`); the allowlist's route
            # patterns admit one, so the path is compared without it
            check("it reaches the model endpoint through the forwarder",
                  sorted({p.split("?")[0] for p in Stub.seen}), ["/anthropic/v1/messages"])
            note(f"{len(env)} names given; {len(os.environ)} on the host; "
                 f"stub saw {len(Stub.seen)} request(s)")
            if not started:
                note(f"stderr: {(r.stderr or '').strip()[:300]}")
    finally:
        fwd.terminate()
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------ 6. arm-run isolation ------
def _mini_store(path: Path, body: str) -> None:
    """The three tables `store_digest` reads, and one row. Not a Nexus store.

    Deliberately not built through `SQLiteRepository`: this section checks the harness's
    copying and digesting, and borrowing the real stack would drag a SQLite floor into a
    script that has to run under the Click venv's interpreter.
    """
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(
        "create table blobs(id integer primary key, body text);"
        "create table revisions(revision_id text primary key, blob_id integer, kind text,"
        " tags_json text);"
        "create table memories(memory_id text primary key, current_revision_id text,"
        " tombstoned integer);")
    con.execute("insert into blobs values (1, ?)", (body,))
    con.execute("insert into revisions values ('r1', 1, 'decision', '[]')")
    con.execute("insert into memories values ('m1', 'r1', 0)")
    con.commit()
    con.close()


def _runner_for(scratch: Path, task: str, attempt: int, master: Path):
    """Import `run_arms_isolated` under a chosen argv and configuration, fresh each time."""
    import importlib
    saved_argv, saved_env = sys.argv[:], os.environ.get("A1_CONFIG")
    cfg = json.loads((BENCH / "a1-config.json").read_text())
    cfg["store_master"] = str(master)
    cfg_path = scratch / f"config-{task}-{attempt}.json"
    cfg_path.write_text(json.dumps(cfg))
    sys.argv = ["run_arms_isolated.py", str(scratch), task, str(attempt)]
    os.environ["A1_CONFIG"] = str(cfg_path)
    try:
        sys.modules.pop("run_arms_isolated", None)
        return importlib.import_module("run_arms_isolated")
    finally:
        sys.argv = saved_argv
        if saved_env is None:
            os.environ.pop("A1_CONFIG", None)
        else:
            os.environ["A1_CONFIG"] = saved_env


def verify_arm_run_isolation() -> None:
    """Two attempts must not share an output path, and no arm-run may share a store.

    Both were true of the previous harness and neither was checked. `ATTEMPT` was parsed and
    recorded but appeared in no path, so three attempts overwrote one another; and one store
    served every arm of every attempt, with the digest taken before the first arm and after
    the last -- detection after the contaminated run rather than prevention before it.
    """
    print("\n6. arm-run isolation: paths per attempt, store per arm-run")
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        master = scratch / "master" / "corpus.db"
        master.parent.mkdir(parents=True)
        _mini_store(master, "the frozen corpus")

        one = _runner_for(scratch, "d1", 1, master)
        frozen = one.store_digest(master)

        # (a) attempts do not share an output path
        two = _runner_for(scratch, "d1", 2, master)
        check("attempt 1 and attempt 2 write to different trees",
              one.OUT != two.OUT, True)
        check("and to different records.json",
              (one.OUT / "records.json") != (two.OUT / "records.json"), True)
        check("nor do the two share an arm's store",
              one.arm_store("nexus") != two.arm_store("nexus"), True)
        note(f"attempt 1 -> {one.OUT.name}/ ; attempt 2 -> {two.OUT.name}/")

        # (b) an arm-run is given a copy, and writing it cannot reach the master
        copy = one.provision_store("nexus")
        check("the nexus arm is given its own store", copy != master, True)
        check("seeded from the master, identically", one.store_digest(copy), frozen)
        check("the arms without memory are given none",
              [one.provision_store(a) for a in ("baseline", "notes")], [None, None])
        import sqlite3
        con = sqlite3.connect(copy)
        con.execute("update blobs set body = 'an arm wrote this' where id = 1")
        con.commit()
        con.close()
        check("a write to the copy changes the copy", one.store_digest(copy) != frozen, True)
        check("and leaves the master untouched", one.store_digest(master), frozen)
        check("so the next arm-run is seeded from the corpus, not the last arm's edit",
              one.store_digest(one.provision_store("nexus")), frozen)

        # (c) the profile withholds the master from every arm, and the store from two of them
        for arm in ("baseline", "nexus", "notes"):
            cwd = one.OUT / "arms" / arm / "repo"
            cwd.mkdir(parents=True, exist_ok=True)
            allow, deny = one.boundary_paths(arm, cwd)
            profile = isolation.write_profile(
                one.OUT / "arms" / arm / "check.sb", cwd, deny, allow, one.FORWARDER_PORT)
            text = profile.read_text()
            m = os.path.realpath(master.parent)
            check(f"[{arm}] the master corpus is denied",
                  f'(deny file-read* (subpath "{m}"))' in text, True)
            check(f"[{arm}] and cannot be written either",
                  f'(deny file-write* (subpath "{m}"))' in text, True)
            # Allows are emitted as given and denies are realpath'd -- `write_profile`
            # resolves one list and not the other. Under a symlinked scratch root the two
            # spellings differ, so each is asked for in the spelling its own list uses, and
            # the divergence is reported rather than hidden by normalising both.
            raw, real = str(one.arm_store(arm)), os.path.realpath(one.arm_store(arm))
            if arm == "nexus":
                check(f"[{arm}] its own copy is readable", f'"{raw}"' in text, True)
            else:
                check(f"[{arm}] the store is denied by name, not by omission",
                      f'(deny file-read* (subpath "{real}"))' in text, True)
                check(f"[{arm}] including writes", f'(deny file-write* (subpath "{real}"))' in text,
                      True)
            if raw != real and arm == "nexus":
                note(f"allow/deny spellings differ here (allows are not realpath'd): "
                     f"{raw} vs {real}. Over-restriction, not a hole -- an allow that does "
                     f"not match blocks the arm loudly -- but the two lists should agree.")

        # (d) a task with no registered prompt is refused, not run with an empty one
        check("an unregistered task is refused at startup",
              _raises_systemexit(lambda: _runner_for(scratch, "nope", 1, master)), True)


def _raises_systemexit(fn) -> bool:
    try:
        fn()
        return False
    except SystemExit:
        return True


def main() -> int:
    print(f"scorer {SC.SCORER_VERSION}   schedule format v{sched.SCHEDULE_VERSION}   "
          f"interpreter {PY_CLICK}")
    verify_scheduling()
    verify_injected_failures()
    verify_round_trip()
    verify_configuration()
    verify_minimal_env_live()
    verify_arm_run_isolation()
    print("\nRESULT:", "all preparation checks hold" if OK else "SOMETHING IS WRONG")
    return 0 if OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
