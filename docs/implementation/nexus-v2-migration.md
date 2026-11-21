# A02 — store identity migration evidence (NXS-02, NXS-07)

Base: `05039abf1ce06415bb78978f33a816aead31411a`, isolated `feat/nexus-memory-v2` worktree. Python 3.13.14, linked SQLite 3.53.1; no dependency changes. All rehearsals used disposable synthetic databases under pytest's temporary directory.

## What changed

005 assigns one store UUID and one space UUID per distinct physical `(namespace, actor)` in the store. Creating or upgrading a v2 store requires `SQLiteRepository(path, owner_schema5=True)`; that same option reopens it. On the first upgrade the owner may also pass `configured_scope` to include an otherwise empty partition. A scope alone is not an upgrade request. Default opens retain the historical 001–004 path and stop at schema 4. Both the legacy constructor and the read-only viewer reject schema 5 with `UnsupportedSchema`; the writable constructor checks before setting WAL mode, then rechecks under its migration lock. The existing migration transaction owns all DDL, UUID allocations, null-attribution sidecar backfills and the version change. Registration can subsequently read the persisted mappings; the migration does not grant access, create graph edges or install mounts. The exact-operation mapping table starts empty. A04 owns its write path. No change to the frozen 001–004 scripts, request digest functions, receipt keys or profile algorithms.

## Rehearsal

Command (before implementation and after):

```sh
uv run pytest -q tests/core/test_migration_spaces.py tests/core/test_migration_b1.py tests/core/test_migration_b2a.py
```

- **Red, exit 1:** first run had a fixture error: an FTS shadow table has no `rowid`. Corrected the snapshot ordering before touching production code. Clean red: **12 failed, 12 passed**. The repository lacked the configured-scope parameter, none of the ten 005 statement boundaries was reached, and the backup helper was absent.
- **Initial focused green, exit 0:** 24 passed, but three historical schema-version assertions had been changed; this masked the default-open regression. The subsequent full run reported eight failures across Atlas, retirement and legacy transport.
- **Repair red, exit 1:** restored those three assertions verbatim, switched the A02 migration fixtures to the owner API and added default/opt-in cases before changing the dispatcher. The focused command (with `--tb=line`) yielded **19 failed, 9 passed**: new and existing legacy stores opened as 5, the explicit option was missing, and `configured_scope` alone did not fail.
- **Repair focused green, exit 0:** **28 passed**. A fresh explicit owner open creates 5; both legacy openers refuse it without changing its journal mode, identity tables or version. Default opens of fresh and frozen-v4 stores produce no 005 tables. Historical migration and retirement tests are unchanged from the base.
- `test_005_preserves_legacy_rows_and_bytes`: compared legacy table contents including FTS storage, sequence, UTF-8 bytes, parent links and digest receipts. Verified six distinct mappings including tombstone-only, receipt-only, repository-only and configured-empty; checked null legacy attribution, reopen stability and replay of pinned v1 and v2 digests with the same key in different actors.
- `test_interrupted_005_rolls_back`: injected an exception immediately after each of five table creations, four insert/backfill stages and the version write. Each fresh inspection found schema 4 and no 005 tables; retry produced one store identity and five partition mappings.
- `test_backup_is_restorable_with_wal`: backed up a committed row still in a nonempty WAL; migrated the disposable original, then opened the independent schema-4 backup in a fresh Python process. Checked the WAL row, manifest equality, integrity and FK checks. Also verified no overwrite and retention of the embedded UUID in a post-upgrade backup.

Full regression after repair: `uv run pytest -q`, exit **0** — **723 passed, 3 skipped, 34 deselected, 1 xfailed** in 115.49 s. Skips, deselections and the expected failure match A01. This includes the historical Atlas, legacy retirement and transport coverage; no installed-host certification is implied.

## Recovery contract and limits

**Stop every legacy writer before the final online backup and keep it stopped through upgrade.** `BEGIN IMMEDIATE` serializes migration but cannot retire an already-running old process. Do not pass `owner_schema5=True` for an unprepared legacy file. The explicit option is a schema selection for trusted callers, not authentication or a substitute for fencing writers. Quiescence and backup are owner preconditions, not automatically detected. Keep the original search and history profile configuration when opening for upgrade.

`backup_store` uses SQLite's online backup API on a read-only source snapshot. It verifies counts, SHA-256 over canonical typed row values per stored table, maximum outbox sequence, schema/UUID, SQLite integrity and FK checks before publishing a standalone copy to a fresh destination. Pre-005 manifests have no store UUID. A backup is not a second independent store.

Rollback is a verified restore, not an in-place version decrement. If writes followed upgrade, retain a separate verified v2 backup/export, preview the loss and obtain explicit owner authorization before restoring. Catalog registration, duplicate UUID rejection and fenced restore/rebind belong to A03; installed-distribution, old-process and release gates remain for the lead/later tasks. No retirement tool, live store, commit, merge or push was used.
