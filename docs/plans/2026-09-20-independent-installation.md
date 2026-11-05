# Nexus installation independent of the source checkout — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Use checkbox steps for tracking. Execute directly; these tasks share the installer, checker, and CI contract and do not need parallel agents.

**Goal:** A developer can install Nexus into a separate environment, move or remove the source checkout, and still run the server, verify the installation, and retrieve previously stored context.

**Architecture:** Keep SQLite storage and the seven MCP tools unchanged. Package the existing lifecycle checker with the service, make the user installer create a non-editable installation from the frozen dependency set, and verify the installed distribution after its build-source directory is removed. Keep development installation through `uv sync` available.

**Tech Stack:** Python 3.12+, Python-linked SQLite 3.51.3+, MCP 2.1.1, hatchling 1.32.0, uv, pytest, GitHub Actions on Ubuntu and macOS.

**Spec:** The next-step planning request in this conversation, the published `docs/experimental-release.md`, and the proposed behavior below. This is a proposed next milestone, not a claim that the new behavior exists or that implementation/publication has been authorized. Proposed version: `0.1.0a2`.

## Verified baseline and why this is next

Checked on 2026-09-20:

- PR #7 is merged. `main` and the peeled `v0.1.0a1` tag point to `fe37e313fb16235ed9f429a2e0f60164853fe181`.
- `v0.1.0a1` is published as a GitHub prerelease: https://github.com/soryko/Nexus/releases/tag/v0.1.0a1 . Do not repeat its merge, tag, or publication.
- Push CI run `35522361203` tested that release commit: 344 product passes / 2 skips; 36 mutation passes; installed lifecycle PASS 9/9; fresh-install PASS 9/9 on `ubuntu-24.04` and `macos-15`.
- The release closeout reports another fresh installation from the published tag, including a successful default interpreter selection on the author's Mac. That remains a recorded host observation, distinct from CI.
- `tools/install.py` currently runs `uv sync --frozen`, which installs the project editable. `tools/check_install.py` is outside the package, and its default server path is derived from the source checkout.
- The known user limitation is therefore concrete: moving the source checkout can break both the server installation and the documented checker command. The next milestone removes that dependence for the recommended installation path.
- Manual backup and Claude Code connection checks were accepted for the first alpha. Automating all of them is not a prerequisite for this packaging milestone.

## Proposed behavior

1. `python3 tools/install.py` creates a normal, non-editable package installation. The existing `--python`, `--venv`, and `--check-only` options remain.
2. `--venv` still defaults to the checkout's `.venv` for compatibility. The user guide recommends an explicit environment outside the checkout, such as `$HOME/.local/share/nexus-memory/venvs/0.1.0a2`, when the source is disposable.
3. Both `nexus-memory` and a new `nexus-memory-check` executable are installed in that environment. The checker locates the server next to its own environment's Python unless `--server` is supplied.
4. `tools/check_install.py` remains as a small compatibility entry point, delegating to the installed checker implementation. It must not add the checkout's `src` directory to `sys.path`.
5. Developers continue to use `uv sync --frozen` for an editable installation with development dependencies. The user installer excludes the development dependency group.
6. Deleting the source is safe only when the installed environment and database are outside that source directory. The Python runtime and virtual environment must remain at their installed paths; this milestone does not make a virtual environment relocatable or bundle Python.
7. Upgrading uses a new versioned environment, then changes the client's executable path while preserving its database path, namespace, and actor. No automatic upgrade or database migration feature is added.

The mechanism is supported by uv's documented `--no-editable` and `--no-dev` options: https://docs.astral.sh/uv/concepts/projects/sync/#editable-installation . Validate the actual workflow using its pinned uv `0.11.21`; the planning host has uv `0.12.15`, so its help output alone is not evidence about the CI binary.

## Global constraints

- Python floor `3.12`; linked SQLite floor `3.51.3`. Read the same enforcing sources as the current installer. Do not substitute the system SQLite executable's version.
- Preserve immutable revisions, idempotent receipts, compare-and-set behavior, namespaces/actors, lexical retrieval, and repository-reference behavior. No storage schema or MCP tool changes.
- Keep dependency versions frozen. Add no runtime dependency for the checker: MCP is already a project dependency.
- Preserve existing checker assertions, all five receipt fields, Unicode/newline sample, report fields, and exit meanings: 0 passed, 1 check/transport failure, 2 missing command or CLI usage failure.
- Keep existing installer failure behavior and built-environment runtime recheck. Explicit interpreter selection must reach the actual build command.
- Test Ubuntu and macOS. Do not expand the Windows support claim.
- Do not use A3 harnesses, model endpoints, host fixture trees, or a user's live memory database. No paid model run is needed or authorized by this plan.
- Do not modify `v0.1.0a1`. A new release requires a reviewed candidate and separate merge/tag/publication authorization.

## Review focus

| Failure mode | Required evidence | Owner |
| --- | --- | --- |
| Source checkout removed, but an editable path or inherited `PYTHONPATH` hid the dependency | Fresh executable processes pass after source removal, with source-path environment overrides removed; an editable control fails only after removal | Task 3 |
| Environment path contains spaces, or Python is a symlink to the base interpreter | Default checker selects the sibling server in the environment; no symlink resolution redirects it to the base runtime | Tasks 1 and 3 |
| Wheel omits SQL migration resources | A fresh database can be created and queried after the source is unavailable | Task 3 |
| Checker wrapper or console entry point loses a failure exit | Real subprocess tests for missing server and closed transport return the documented nonzero code | Tasks 1 and 3 |
| New runtime opens the wrong database or makes rollback depend on a deleted checkout | An isolated old-release-to-candidate smoke preserves explicit database identity and records; documented update preserves client launch arguments | Task 4 |

## File map

| File | Responsibility |
| --- | --- |
| `src/nexus_memory/install_check.py` — new | The existing MCP lifecycle check, packaged with the service |
| `tools/check_install.py` — simplify | Compatibility entry point into the packaged checker |
| `tools/install.py` — modify | Build a normal runtime installation and print commands that work without source files |
| `pyproject.toml`, `uv.lock` — modify | Add the checker console entry point; later set the candidate version without dependency upgrades |
| `tests/tools/test_install_check.py` — new | Checker environment selection and module entry-point behavior |
| `tests/tools/test_release_tools.py` — retain/adapt | Existing installer failures and compatibility-wrapper failure behavior |
| `.github/scripts/check_installed_distribution.py` — new | Model-free installation/source-removal verification, using disposable fixtures it creates itself |
| `.github/workflows/tests.yml` — modify | Run the new installed-distribution checks on both platforms |
| `README.md`, `docs/experimental-release.md` — modify | User installation, developer installation, checker, and update instructions |
| `docs/releases/0.1.0a2.md` — new | Proposed release behavior, evidence, and limits |

## Task 1 — Package the existing lifecycle checker

**Interfaces:** Preserve `check(server: str, keep: bool = False) -> dict` and its current JSON structure. Add `default_server(executable: str) -> Path` and `main(argv: list[str] | None = None) -> int`. Retain `_attempt` and the existing structured-result decoding internally so the release verification driver can reuse the actual installed MCP client path.

- [ ] Fetch current `main`, inspect any changes since the verified baseline, then create a new implementation worktree and branch `release/independent-installation`. Follow the worktree skill at execution time. Carry this plan into that branch.
- [ ] Add a regression test for locating the sibling server without following the virtual environment's Python symlink:

```python
def test_default_server_keeps_the_environment_path(tmp_path):
    import sys
    from nexus_memory.install_check import default_server

    python = tmp_path / "runtime with spaces" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    assert default_server(str(python)) == python.with_name("nexus-memory")
```

- [ ] Run the new test and confirm it fails before the module exists.
- [ ] Move the existing checker logic into `src/nexus_memory/install_check.py`. Do not rewrite its MCP protocol or assertions during the move. Remove source-checkout discovery from that module.
- [ ] Implement environment-based default selection. Do not call `resolve()` on the Python executable before finding its parent:

```python
def default_server(executable: str) -> Path:
    name = "nexus-memory.exe" if os.name == "nt" else "nexus-memory"
    return Path(executable).absolute().with_name(name)
```

- [ ] Make argument parsing accept `argv`, retain `raise SystemExit(main())`, and retain a module entry point so `python -m nexus_memory.install_check` also works.
- [ ] Add `nexus-memory-check = "nexus_memory.install_check:main"` under `[project.scripts]`.
- [ ] Replace the source checker with delegation only:

```python
from nexus_memory.install_check import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] Replace diagnostic instructions that require a surviving `tools/install.py` with an explicit runtime-version probe and the release guide URL. The compatibility script may still be mentioned as an alternative while source is available.
- [ ] Extend the real subprocess failure tests to the module entry point. For example, launch `[sys.executable, "-m", "nexus_memory.install_check", "--server", str(missing)]` and require exit 2. Use the existing real executable that closes without MCP to require exit 1 and a failed JSON report. Keep wrapper tests rather than replacing them all with function mocks.
- [ ] Run the new tests and existing `tests/tools/test_release_tools.py` from a developer environment created with `uv sync --frozen`. Review the moved logic for accidental changes, then commit this independently reviewable unit.

**Acceptance:** The checker is part of the package; module and compatibility entry points preserve behavior; no default path depends on a checkout.

## Task 2 — Install a normal package into the selected environment

**Interfaces:** Preserve the current installer flags and runtime probe. The successful output names both installed executables by absolute path. The source checkout is needed at build/update time, not at server/checker runtime.

- [ ] Preserve interpreter selection, `UV_PROJECT_ENVIRONMENT`, removal of conflicting inherited `UV_PYTHON`, and the built-environment recheck. Change the sync command to:

```python
["uv", "sync", "--frozen", "--no-editable", "--no-dev", "--python", chosen]
```

- [ ] Keep `.venv` as the default destination. Do not silently migrate or remove an existing user's environment or data. Document that a separate versioned destination is the recommended user path.
- [ ] Print the installed `nexus-memory-check` command for verification, instead of a command requiring `tools/check_install.py`. Include whether the chosen environment is inside the checkout so the user knows whether deleting that directory would delete the environment itself.
- [ ] Build a fresh, separate environment using the real installer and the pinned qualifying interpreter. Run its checker executable directly, without `uv run` or a source-tree Python path.
- [ ] Inspect `nexus_memory.install_check.__file__` and distribution metadata using that environment's Python. The module must reside in the environment's installed package directory; `dir_info.editable` must not be true when `direct_url.json` is present. These checks supplement, not replace, the source-removal test in Task 3.
- [ ] Verify that pytest/hypothesis are absent from this fresh runtime environment and that the packaged checker still works. Product tests continue in the separate developer/CI suite environment.
- [ ] Run the existing installer refusal tests; commit the change once the live installation and lifecycle pass.

**Acceptance:** The user installer creates a normal package installation with runtime dependencies, and its printed verification command works independently of source-script paths.

## Task 3 — Prove independence through the production install path

**Files:** `.github/scripts/check_installed_distribution.py`, `.github/workflows/tests.yml`.

**Driver interface:** `main(argv: list[str] | None = None) -> int` with `--repo PATH`, `--python PATH`, `--artifacts PATH`, and optional `--editable-control`. It uses uv from the configured CI PATH. It creates and mutates only disposable source copies/environments under a temporary directory beside its artifact directory; it never moves or deletes the caller's checkout. It records the input commit and subprocess results.

- [ ] Implement the driver as stdlib orchestration. Use `git archive HEAD` from `--repo` to create its build-source copy. Keep the environment, fixture database, and evidence outside that copy. Include spaces in the environment/source fixture paths.
- [ ] Invoke the copied `tools/install.py` with the explicit interpreter and separate environment. Do not create that environment using `uv sync` first. Fail immediately on installer failure, preserving stdout/stderr.
- [ ] Use the installed Python in fresh subprocesses for package-origin and version checks. Remove inherited `PYTHONPATH` and `PYTHONHOME` from those subprocesses. Run outside either source checkout.
- [ ] Before hiding the source, run the installed checker once and seed a separate fixture database through the installed package's actual MCP client helper. Persist the real receipt and expected content in the test artifact directory:

```python
# Executed by the installed environment's Python, with server and db as argv.
import json
import sys
from nexus_memory.install_check import CONTENT, _attempt

server, db = sys.argv[1:3]
receipt = _attempt(server, db, "distribution-check", "local", [
    ("record", {"content": CONTENT, "kind": "procedure",
                "idempotency_key": "distribution-check-1"}),
])[0]
print(json.dumps({"receipt": receipt, "content": CONTENT}))
```

- [ ] Rename the disposable source directory out of its original path. Confirm that the original path no longer exists. Do not claim that a change of working directory alone removes source access.
- [ ] Start `nexus-memory-check` as a fresh executable process and require all nine checks. This creates a new database after source removal and therefore exercises packaged SQL migration resources as well as Python modules.
- [ ] Start another fresh server on the pre-removal fixture database; require the original content bytes, memory/revision IDs, lexical search hit, and whole-receipt retry with count still one. Reuse the installed protocol helper, not fabricated runner records.
- [ ] Repeat the package-origin probe in a fresh Python process after source removal. This rules out a parent process's import cache standing in for an installed module.
- [ ] Write `distribution-check.json` containing candidate SHA, OS/runtime versions, installed version/module locations, source-path absence, subprocess exits, lifecycle result, and persisted-memory result. Missing or failed steps make the driver exit nonzero. Never derive success from a missing report.
- [ ] Add an editable negative-control mode for this verification driver only. In a separate disposable environment, perform the old editable sync, require a passing check while source exists, then hide the source and require the new executable invocation to fail because the package can no longer be imported. A failure before hiding source does not satisfy this control. Keep its report separate from the passing candidate report.
- [ ] Run the control once during implementation and preserve its evidence. CI must always run the normal candidate path; the control need not double every routine CI job.
- [ ] Extend the existing `fresh-install` matrix (`ubuntu-24.04`, `macos-15`) to run this driver using `uv python find --system "$UV_PYTHON"`. Keep uv `0.11.21`, Python `3.13.14`, per-host linked-SQLite checks, timeouts, pipefail, and per-platform evidence uploads.
- [ ] Preserve the existing product suite and mutation job. Do not add benchmark/A3 jobs to this release workflow. Review actual job logs and archive results before committing/declaring this gate complete.

**Acceptance:** Both platforms run the server and bundled checker after the original source path is unavailable. Fresh and existing fixture databases work. The old editable mechanism demonstrably fails the same removal check.

## Task 4 — Document installation and verify an upgrade from the released alpha

**Files:** `README.md`, `docs/experimental-release.md`, `docs/releases/0.1.0a2.md`, `pyproject.toml`, `uv.lock`.

- [ ] Show the recommended separate environment explicitly. One valid user-selected location on either supported OS is:

```bash
uv python install 3.13.14
nexus_runtime="$HOME/.local/share/nexus-memory/venvs/0.1.0a2"
python3 tools/install.py \
  --python "$(uv python find --system 3.13.14)" \
  --venv "$nexus_runtime"
"$nexus_runtime/bin/nexus-memory-check"
```

- [ ] Explain that the directory is an explicit installation choice, not a change to Nexus's existing platform-dependent database defaults. Keep the existing interpreter-repair recipes and both runtime floors.
- [ ] State the user/developer distinction precisely: installer = normal runtime package; `uv sync --frozen` = editable developer environment with dev dependencies. Running a plain `uv sync` later against the runtime environment can change its installation mode; use the installer for user upgrades.
- [ ] Update Claude Code setup to point at the new environment's server executable. For an existing user, change only the executable path and preserve their actual `--db`, namespace, actor, and optional `--repo` arguments.
- [ ] Document stop → back up → build new environment → verify → switch client executable → restart. Retain the old environment until the upgrade is checked. Building the new environment must not change the old environment or the user's database.
- [ ] Perform one isolated upgrade smoke using the real `v0.1.0a1` server and candidate server. Use a temporary database, record and revise a memory with the old server, stop it, then use the new server to verify current content, historical revision, IDs, search, and receipt replay. This checks compatibility of the installation change; it is not a schema-migration feature.
- [ ] Verify rollback in that fixture using the retained old environment and a separately created pre-upgrade backup. Do not infer that virtual environments can be moved or that future schema downgrades are safe.
- [ ] State that deleting the source is supported only for a normal installation whose environment and data remain outside it. Keep the interpreter/environment paths stable.
- [ ] Set metadata to `0.1.0a2`, regenerate the lockfile, and verify no unrelated dependency upgrades. Do this before the final candidate CI and client check.
- [ ] Write release notes focused on the new installation/checker behavior and the unchanged memory contracts. Continue to state that coding benefit is unproven.
- [ ] Use absolute tag-pinned documentation links in the GitHub release body. The existing `v0.1.0a1` body contains the repository-relative `../experimental-release.md`; prepare a correction to `https://github.com/soryko/Nexus/blob/v0.1.0a1/docs/experimental-release.md` as a small metadata follow-up. Do not rewrite the old tag or silently edit published metadata during plan preparation.

**Acceptance:** A user can follow the new path without preserving a source checkout, and an existing alpha user's fixture data remains accessible after the executable changes.

## Task 5 — Finish the candidate and release only after approval

- [ ] Open a focused PR against current `main` with provenance, an explicit statement that `v0.1.0a1` is already released, and links to the candidate's CI and source-removal evidence. Keep the evaluation PR separate.
- [ ] Run the product suite and mutation controls, the two-platform installed-distribution checks, and the isolated upgrade smoke. Report actual counts; do not force new tests into the old 344 count.
- [ ] On an available Claude Code host, verify the new environment's executable connects after its disposable source checkout is unavailable. Use a temporary server entry, no model prompt, and preserve the user's existing entry. Record the client version and actual connection result.
- [ ] Review the diff specifically for changes to storage/transport behavior, checkout-relative imports, omitted SQL resources, and source-dependent user commands. Resolve a failed acceptance gate without expanding the milestone into retrieval research.
- [ ] Present the exact candidate and release notes for merge/tag/publication approval. If publication is authorized, remove any pending-tag notices before the final commit, merge, verify resulting-main CI, tag the verified commit as `v0.1.0a2`, and publish the prerelease.
- [ ] From a fresh clone of the published tag, install into a separate environment, discard that temporary source checkout, and run the installed checker. Record the tag's resolved commit and result. Do not overwrite an existing tag if the proposed name has appeared meanwhile.

**Done:** A published installation demonstrably runs and verifies itself without its source checkout. No new memory-quality claim is made.

## Scope, estimate, and what follows

**Planning estimate:** 2–3 focused working days for the checker move, installer change, two-platform evidence, documentation, and upgrade smoke, assuming CI and a Claude Code host are available. Review/publication timing is separate.

This plan does not include a new backup CLI, automatic extraction, embeddings, graph retrieval, compression, a web dashboard, more clients, Windows support, PyPI publication, or binary distribution. Those are separate proposals, not hidden acceptance gates.

After this milestone, collect a small set of user-provided observations from ordinary development: installation friction, retrieval successes/misses, stale memories, and repeated manual steps. Use them to select the next product improvement. Such observations are usability evidence, not a controlled estimate of memory benefit. A3 remains a separately authorized evaluation track with its own budget and interpretation limits.
