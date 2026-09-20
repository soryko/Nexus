# Nexus experimental developer release implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task. This is a small, coupled release change; execute it directly without parallel agents. Checkboxes track implementation, not evidence already obtained.

**Goal:** A new developer can follow the documentation to install Nexus, connect Claude Code, store context, stop the server, and retrieve the same context after restarting it.

**Architecture:** Keep the existing local SQLite service and MCP stdio interface. Create a focused release branch from `main`, carry over the existing startup contention repair and developer tools, and verify the source installation users will actually receive. Publish a tagged GitHub prerelease only after the concrete candidate has been reviewed and publication authorized.

**Tech stack:** Python 3.12+, Python-linked SQLite 3.51.3+, MCP 2.1.1, uv with the frozen lockfile, pytest, GitHub Actions, Claude Code.

**Spec:** The release goal and separation from A3 agreed in this conversation, made concrete by the acceptance table below. The proposed first distribution is a tagged source checkout, with `v0.1.0a1` as the proposed version. PyPI and standalone binary distribution are outside this milestone.

## Verified starting point

Inspected on 2026-09-20:

- Evaluation branch and draft PR #6: `f5e75cad6321640992fae87c49585e7ffb2b73b2`.
- Remote `main`: `0f5e67120fd30076c94794288f2a18b8c47de163`. Fetch it again before branching; do not assume this remains current.
- PR #6 contains 352 changed files. It is not the proposed release PR.
- Existing release work is commit `ebd82fd3b522a4013515a113395b71446df33e13`: installer, lifecycle check, release guide, README additions, and a CI step.
- Product repair `2cd531f9d7c274a065533e58ba3fde8582f8c269` is present on the evaluation branch but absent from the inspected `main`. It repairs contention while switching a database into WAL mode and includes a deterministic regression test.
- CI run `35511742760` on `f5e75ca` reports 334 product passes / 2 skips, 36 mutation passes, 232 benchmark passes, and lifecycle PASS 9/9. These are evidence about that evaluation commit, not about the future extracted release branch.
- The current CI lifecycle step uses an environment already created by `uv sync`; it does not execute the installer's environment-creation path.
- `docs/experimental-release.md` incorrectly says `status` reports the database path. Neither status schema has such a field.
- No GitHub releases or remote tags were returned by the inspections. The package metadata currently says `0.1.0`.

## Global constraints

- Preserve storage, revision, idempotency, namespace/actor, search, and reference contracts. No schema redesign or new MCP tools.
- Carry forward the startup contention fix. Do not derive the release by copying all of PR #6 onto `main`.
- Linux and macOS are the release targets; Claude Code is the first documented client. Describe tested configurations precisely. Windows remains unverified.
- Runtime acceptance is based on the selected interpreter's actual linked SQLite, not its Python version, installation source, or a system `sqlite3` executable.
- No paid model invocation is needed for installation, MCP lifecycle, backup, or client connection checks. A3 remains unapproved and separate.
- Preparation ends with a reviewable release PR, exact-commit evidence, and draft release notes. Merging, tagging, and publishing require authorization for that concrete result.
- No new lint/typecheck gate is implied: the repository does not currently configure one.
- A working memory service is the release claim. Improved coding outcomes are not a release claim or an acceptance gate.

## Acceptance criteria

| User outcome | Evidence required on the release candidate |
| --- | --- |
| Install from documented source | A fresh checkout and environment, created by `tools/install.py`, succeeds on the documented Linux and macOS configurations. |
| Select a runnable interpreter | Candidate and built-environment logs record both Python and linked SQLite; an unsuitable explicit interpreter fails clearly without silently selecting another. |
| Connect a client | A recorded Claude Code version connects using the documented absolute executable and database paths. Parsed configuration or pending approval alone is not connection evidence. |
| Persist and retrieve | The installed executable passes store, shutdown, restart, exact-content retrieval, search, and idempotent retry checks over MCP. |
| Back up and recover | The documented SQLite backup command is run against a seeded database; a new MCP server reading the copy returns the recorded content and IDs. |
| Diagnose installation failure | Missing uv, missing executable, and an executable that closes before serving MCP produce actionable messages and nonzero process exits. |
| Reproduce the release | Candidate SHA, runtime versions, CI links, lifecycle JSON, and client/backup observations are associated with one candidate. |

## Task 1 — Extract a focused release branch

**Files:** Existing `src/nexus_memory/storage/sqlite.py`, `tests/core/test_memory_core.py`; add `tools/install.py`, `tools/check_install.py`, `docs/experimental-release.md`; adapt `README.md` and `.github/workflows/tests.yml`.

- [ ] Fetch `main` into an explicit remote-tracking ref; the current local checkout does not have `origin/main` populated.

```bash
git fetch origin refs/heads/main:refs/remotes/origin/main
git worktree add ../Nexus-release -b release/experimental-v0.1 origin/main
```

- [ ] In the new worktree, check whether the freshly fetched base already contains the startup repair. If absent, cherry-pick `2cd531f9d7c274a065533e58ba3fde8582f8c269` with `-x` to retain provenance.
- [ ] Port the three new release files from `ebd82fd3b522a4013515a113395b71446df33e13`. Apply its README and CI changes selectively against the new base. Do not replace the workflow with the evaluation branch's entire workflow.
- [ ] Preserve the concise disclosure that no coding benefit has been demonstrated. Link to A1 evidence at an immutable evaluation commit, for example `https://github.com/soryko/Nexus/blob/f5e75cad6321640992fae87c49585e7ffb2b73b2/benchmarks/agent/CLOSEOUT-a1.md`; do not introduce relative links to files absent from the release branch.
- [ ] Copy this plan into the new branch and retain it there during implementation.
- [ ] Run `tests/core/test_memory_core.py::test_initialization_waits_out_a_contended_journal_switch` using a qualifying interpreter. Inspect `git diff --name-only origin/main...HEAD` for unintended campaign files before opening the PR.

**Done when:** The release branch contains the necessary product fix and release tooling, with no new A3 harness, launch records, corpus, or host-fixture dependency.

## Task 2 — Finish the documented installer and its failure paths

**Modify:** `tools/install.py`, `tools/check_install.py`.

**Add:** `tests/tools/test_release_tools.py`, collected by the existing `testpaths = ["tests"]` configuration.

- [ ] Keep interpreter discovery, `--python`, `--venv`, both source-derived floors, `uv sync --frozen`, and the built-environment recheck. Do not replace the existing installer.
- [ ] After a candidate is selected and `--check-only` has been handled, detect missing uv before environment creation. Return exit 2 with its installation prerequisite and retry command. Catch process-launch `OSError` so losing the executable between the check and launch also produces a diagnosis.

```python
if shutil.which("uv") is None:
    print("uv is required to build the environment. Install uv, then rerun this command.")
    return 2
```

- [ ] Remove unqualified claims that all managed macOS Python builds link a particular old SQLite. State that interpreter builds vary and that the probe decides suitability.
- [ ] Preserve the existing three-process lifecycle. Use a short content sample containing Unicode and a newline, and compare its UTF-8 bytes after retrieval. Keep the search term explicit.
- [ ] Make the retry check verify all five receipt fields (`memory_id`, `revision_id`, `operation_id`, `durable_seq`, `operation`) against the first write, while retaining the independent active-count check. This makes the check match its existing full-receipt replay claim.
- [ ] Correct the checker's `--json` help/docstring to describe writing a JSON file.
- [ ] Add focused tests for: reporting both failed floors; refusing to substitute for an explicit invalid interpreter; missing uv returning exit 2 without a traceback; missing server returning exit 2; and a real temporary executable that exits without serving MCP returning exit 1.
- [ ] Invoke actual CLI entry points for exit-status tests, with an absolute test interpreter and controlled PATH. Put a finite timeout on the transport negative control. Use real server output for the successful lifecycle check; synthetic floor tests are unit evidence only.

**Done when:** The installer succeeds through its real creation path, failures are actionable, and the installed service passes the strengthened lifecycle assertions. No new service behavior is introduced by these checker changes.

## Task 3 — Make the release guide independently followable

**Modify:** `README.md`, `docs/experimental-release.md`.

- [ ] Start with prerequisites, clone/checkout, and `cd`; show the proposed immutable prerelease tag and label it as pending until publication. Link to uv's installation instructions. Explain how to supply an explicitly probed Python.
- [ ] Provide one measured Linux recipe and one measured macOS recipe. Linux can start from CI's pinned uv/Python pair. macOS can start from Homebrew's `python@3.13`, selected with its absolute executable path. Neither recipe may claim success without printing and checking linked SQLite.
- [ ] Show the lifecycle command before client setup so an MCP client is not the first place a runtime failure appears.
- [ ] Use one canonical Claude Code user-scope configuration with explicit `--db`, `--namespace`, and `--actor`. Keep project scope as an alternative with its approval requirement. Use a distinct temporary server name during verification and do not overwrite the owner's existing entry.

```bash
claude mcp add nexus-memory-release-check -s user -- \
  /absolute/path/Nexus/.venv/bin/nexus-memory \
  --db /absolute/path/nexus-data/memory.sqlite3 \
  --namespace my-repo --actor local
```

- [ ] Explain that JSON executable and database paths must be absolute; shell-style `~` or environment expansion must not be assumed in JSON.
- [ ] Remove the false `status` path claim. Document actual defaults: macOS `~/Library/Application Support/Nexus Memory/memory.sqlite3`; Linux `$XDG_DATA_HOME/nexus-memory/memory.sqlite3` only when that variable is absolute, otherwise `~/.local/share/nexus-memory/memory.sqlite3`. Keep this a documentation correction, with no status schema change.
- [ ] Keep SQLite's online backup API as the primary procedure. Encode the source path with `Path(...).resolve().as_uri() + "?mode=ro"`, so spaces and URI-significant filename characters are handled. Refuse or choose a new destination when a backup file already exists in the walkthrough.
- [ ] Run that exact backup example against a temporary seeded store, including a path containing spaces. Launch the installed server against the resulting copy and verify content, IDs, and count. Do not use the user's live database.
- [ ] Document updating the source checkout and rebuilding after backup, stopping/restarting the client server process, and restoring the matching backup if rollback is needed. No automatic backup or migration tooling is added.
- [ ] Scope the historical client observation accurately: a pending project entry is not a verified connected project entry. Report only the scope actually connected in the new candidate check.
- [ ] Keep known limits brief and prominent: lexical current-revision search, local trusted-user operation, reference existence versus prose truth, and unproven coding benefit.

**Done when:** Someone starting outside the repository can follow one complete path through install, verification, connection, database location, and recovery without undocumented host state.

## Task 4 — Verify the actual installation in CI and on the client

**Modify:** `.github/workflows/tests.yml`.

- [ ] Preserve `main`'s product suite, separate mutation run, pinned Actions, runtime verification, pipefail behavior, and failure artifacts.
- [ ] Add a fresh-install job for Linux and macOS. Do not assume that an interpreter's linked SQLite is identical across platforms. Log OS/architecture, executable, Python, SQLite, uv, and candidate SHA.
- [ ] Install only the prerequisite runtime/tooling first. Invoke `tools/install.py` directly to create a previously nonexistent environment, rather than using `uv run` to create the project environment before the installer runs.
- [ ] On Linux, the core command sequence can be:

```bash
nexus_repo="$(pwd -P)"
nexus_env="$RUNNER_TEMP/nexus-release-venv"
nexus_python="$(uv python find "$UV_PYTHON")"
"$nexus_python" tools/install.py --python "$nexus_python" --venv "$nexus_env"
mkdir -p "$nexus_repo/artifacts"
cd "$RUNNER_TEMP"
"$nexus_env/bin/python" "$nexus_repo/tools/check_install.py" \
  --server "$nexus_env/bin/nexus-memory" \
  --json "$nexus_repo/artifacts/install-check.json"
```

- [ ] On macOS, choose the measured Homebrew interpreter explicitly and run the same installer/check sequence. If its linked SQLite fails, resolve or narrow the documented recipe; do not weaken the floor or call that platform verified.
- [ ] Scope CI's `UV_PYTHON` request to the Linux provisioning path. Do not pass a conflicting inherited Python request to the macOS installer's `uv sync`; verify that its built environment uses the explicitly selected interpreter. This is a CI environment check, not an assumption that both platforms share one build.
- [ ] Keep lifecycle execution outside the checkout's working directory, with the installed console path explicit. This verifies the supported source installation; do not describe it as a wheel installation test.
- [ ] Upload per-platform logs and lifecycle JSON with distinct artifact names. Ensure failures from installation or checking determine the job exit status.
- [ ] Run the product suite and mutation controls on the extracted release candidate. Record new counts rather than requiring them to equal the old counts after adding release-tool tests.
- [ ] On an available Claude Code host, record the client version and actual connected status using the documented setup. Verify tool discovery without sending a model prompt. Record pending approval separately; remove only the temporary entry created for this check afterward.
- [ ] Confirm CI results from job output on the exact candidate SHA. A product suite pass, lifecycle pass, and client connection are three different observations; retain all three.

**Done when:** Linux and macOS fresh installations pass, the product regressions pass, and one documented Claude Code scope connects on the candidate. If a client host is unavailable, the PR remains reviewable but that acceptance item stays explicitly unverified.

## Task 5 — Prepare the reviewable prerelease

**Modify:** `pyproject.toml`, `uv.lock`.

**Add:** `docs/releases/0.1.0a1.md`.

- [ ] Set the proposed prerelease version to `0.1.0a1` in project metadata and regenerate the lockfile. Inspect the lock diff: no unrelated dependency upgrades are part of this task.
- [ ] Make this version change before the final candidate verification so logs describe the version intended for release.
- [ ] Write concise release notes: who it is for, install and verification commands, durable capabilities, tested platforms/client, backup guidance, and current evidence limits.
- [ ] Open a focused draft PR against `main`, with provenance for the ported commits and links to candidate CI/verification evidence. Leave PR #6 as the separate draft evaluation PR.
- [ ] Put exact SHA and CI links in the PR evidence rather than a committed document that attempts to contain its own hash. Record host checks with their tested SHA and tool versions.
- [ ] Review the final diff for missing files, broken documentation links, external host paths, and accidental evaluation dependencies. Verify that the prerelease install command names the intended tag, never a moving evaluation branch.

**Done when:** The user can inspect one focused PR and proposed release notes, with every release acceptance item either satisfied or explicitly outstanding. This is the end of implementation preparation.

## Task 6 — Publish after authorization

**External operations:** Merge the focused PR, create the approved tag, and publish a GitHub prerelease.

- [ ] Present the concrete candidate and request approval for merge/tag/publication only after the preparation above is complete.
- [ ] After authorization, merge using the approved method. If the resulting commit differs from the candidate, verify CI on that resulting commit before tagging it.
- [ ] Create `v0.1.0a1` at the verified release commit and publish the prepared notes with GitHub's prerelease flag. Do not overwrite an existing tag.
- [ ] Check out the published tag in a fresh directory, run the documented installer and lifecycle once, and link the release URL and verification result.

**Done when:** A new developer can install from the published tag and reproduce the release's lifecycle check. A3 has not been run or made a prerequisite.

## Review focus and finite stopping rule

1. Is the startup fix preserved without importing the campaign?
2. Did CI run the installer into a fresh environment and check both actual runtime versions?
3. Does the installed executable carry context across process boundaries and replay the complete receipt without duplication?
4. Is connected-client evidence distinct from configuration parsing and from the Python MCP lifecycle?
5. Are database paths and backup instructions accurate for the documented platforms?
6. Does every success claim identify the commit and environment it actually tested?

Stop preparation when these checks and the acceptance table are satisfied. Do not add retrieval features, more evaluation arms, generalized sandbox work, or additional client integrations to obtain a stronger marketing claim.

**Planning estimate:** 1–2 focused working days for extraction, bounded tool/doc changes, CI, and client/backup verification, assuming GitHub Actions and a Claude Code host are available. This is an effort estimate, not a delivery promise; reviewer/publication timing is separate. Most of the service and release tooling already exists.
