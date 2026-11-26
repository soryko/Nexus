# Nexus V2 execution log — A00

The isolated `feat/nexus-memory-v2` worktree started clean (`git status --short`: no output) at `4ce3a5bbafdff74e2cdf98858c9e579d64790093`, exactly the handoff baseline. The original feature worktree and its unpublished changes are not inputs. All eight transfer checksum entries matched.

## Observed baseline

macOS arm64, non-root UID 501; uv 0.11.21, CPython 3.13.14, linked SQLite 3.53.1 with FTS5, Git 2.54.0. Linux is also a required platform but was not exercised here. Lockfile unchanged; installed MCP 2.1.1, pytest 9.1.1 and Hypothesis 6.167.1.

| Command | Exit | Evidence |
| --- | ---: | --- |
| `uv sync --frozen --python 3.13.14` | 0 | Fresh worktree-local environment |
| `uv run python .github/scripts/verify_runtime.py` | 0 | SQLite floor, FTS5, non-root and Git controls passed |
| `uv run pytest -q` | 0 | 670 passed, 3 skipped, 34 deselected, 1 xfailed in 145.04 s |

Two mutation-matrix tests were skipped without their opt-in flag. The Codex host test lacked an installed adapter binding. The existing Claude plugin tool-name gate was an expected failure. These are not host certifications. No V2 behavior is enabled by A00.
