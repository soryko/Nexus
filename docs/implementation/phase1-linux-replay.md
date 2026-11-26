# Cross-host plan, Phase 1 gate on Linux (27 November 2026)

The Phase 1 gate in [the cross-host plan](../plans/cross-host-project-memory.md) §5 requires identity, mode, concurrency and restart tests to pass on Linux under umask 002 and umask 022. PR #30 targets the v2 branch rather than main, so GitHub CI does not run on it. The gate was therefore replayed locally, the same way as the 24 November v2 replay.

**Environment (measured).** Docker Desktop on macOS 26.1. Ubuntu 24.04.5 LTS, linux/arm64, running natively rather than under amd64 emulation. Git 2.56.1 from ppa:git-core. uv 0.11.21. Python 3.13.14 and SQLite 3.53.1, pinned with `UV_PYTHON` as in `tests.yml`. The code runs as an unprivileged user `dev` whose group `dev` is private: no other member, and no other account uses it as its primary group. This is local evidence, not a GitHub CI record. CI's runners are amd64.

## Result at `6a334f6`

| Run | Suite | Stage A protocol drill | Stage A recovery drill | B2 mutation matrix | DSH bridge |
|---|---|---|---|---|---|
| umask 022 | 968 passed, 0 failed, 14 skipped, 1 xfailed | passed | passed | 36 passed | 5 passed |
| umask 002 | 968 passed, 0 failed, 14 skipped, 1 xfailed | passed | passed | 36 passed | 5 passed |

Several tests were skipped because the image lacks a tool: the Antigravity correlation probes (no node), the `agy` CLI test, and the PR #17 branch checks. Others were skipped because the platform behaves differently:
- the macOS letter-case test;
- the moved-catalog-repo recovery test, because Linux has no birth time;
- the `NEXUS_E2E_CODEX_SERVER` end-to-end test.

The Atlas UI suite (`-m ui`, which needs Playwright browsers) was not run here.

**Private-group negative control** (`tools/linux_replay/private_group_probe.sh`, 7 of 7 as expected). It states each expected outcome itself, rather than asking the code's own `private_group()` predicate, which is what `test_umask_002_checkout_follows_the_private_group_rule` compares against. Under umask 002 a fresh `.git` is `drwxrwxr-x dev`:
- **Accepted:** the repo while `dev` is the only member of its group.
- **Refused:** a `.git` whose group is a shared group, `team`.
- **Refused:** the same private repo after another account joins group `dev`.
- **Accepted again:** after that account leaves.
- **Refused:** when another account uses `dev` as its primary group.
- **Accepted:** umask 022 repos, the control.

## What the replay found

The first replay, at `ccc2798`, failed `test_inspect_scope_classifies_all_states_without_mutation` under both umasks. It passed on the base `cea35c2` in the same image, so it was a Phase 1 regression on Linux. The immediate cause was the test's stub. The real hazard behind it: `os.stat` has no birth time on Linux and inodes are reused. Identity was therefore only (device, inode), and an unrelated new repo could match a deleted enrolled one. It would be reported "moved: run recover", and recovering it would have given it the old repo's project and memory.

Fixed in `25cce3f`. A move is trusted only with a valid birth time. On Linux, a moved checkout is a new repo. The same commit fixed path-move `recover` for catalog repos, which have no per-project database. `6a334f6` makes the two move tests state the Linux expectation.

The three other failures in that first run were the instrument's fault, not the product's:
- The image was built from a `git archive` export, which has no `.git`, so both drills failed at `git rev-parse`.
- `uv` was not on `PATH`, so the wheel and installer tests failed.

Each of those failed the same way on the base commit, and the recipe below fixes both.

## Reproduce

```sh
ctx="$(mktemp -d)"
git bundle create "$ctx/src.bundle" HEAD
cp tools/linux_replay/Dockerfile tools/linux_replay/private_group_probe.sh "$ctx/"
docker build --platform linux/arm64 --build-arg COMMIT="$(git rev-parse HEAD)" -t nexus-linux-replay "$ctx"
for m in 022 002; do docker run --rm nexus-linux-replay bash -c "umask $m; .venv/bin/python -m pytest -q -p no:cacheprovider"; done
docker run --rm --user root -v "$ctx/private_group_probe.sh:/probe.sh:ro" nexus-linux-replay bash /probe.sh
```
