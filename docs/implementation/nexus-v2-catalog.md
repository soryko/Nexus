# A03 lead-review receipt

Base `771e39c`, Stage A only. Python 3.13.14, linked SQLite 3.53.1. All stores, catalogs, backup images and enrollments exercised here were synthetic test fixtures.

## Verification

- Before implementation: `uv run pytest -q tests/spaces/test_catalog.py tests/spaces/test_mounts.py --tb=line` — exit 1, **8 failed** in 0.11s. Tests collected and each failed at the first missing interface (`nexus_memory.spaces.catalog`); this was an absent-feature red, not eight independent assertion failures.
- Required gate: `uv run pytest -q tests/spaces/test_catalog.py tests/spaces/test_mounts.py tests/core/test_repository_identity.py tests/tools/test_project_scope.py tests/atlas/test_binding.py` — exit 0, **51 passed** in 7.06s.
- Compatibility: `uv run pytest -q tests/core/test_migration_spaces.py tests/core/test_migration_b1.py tests/core/test_migration_b2a.py` — exit 0, **28 passed** in 0.26s. Ordinary construction still targets schema 4 and legacy opens still refuse 5; the new migration identity comes from a durable owner catalog allocation only in the catalog-directed path.

Registration tests inject an exception after the store transaction commits, before catalog installation. A reopened catalog refuses a copy even during pending registration and reconstructs the original two partitions using the persisted IDs. Duplicate locator, same-ID file replacement, retired state and future version are refused. Create replay resolves the same space; same title with a different operation creates a separate file and physical scope.

Mount tests cover one default writable destination, denial of another simultaneous mount, forged grant/unknown session, no inherited grant for another owner, switch epoch invalidation, retained exposure, read-only selection and explicit restore. A failed restore with the wrong store cannot reactivate the mount. Rebinding the verified backup does not revive old grants or permit registration of the old copy.

## Scope and operating assumptions

- This is the trusted host interface, not a model tool, launcher, or admin CLI. Only a trusted registrar may call `launch_context` for a genuinely fresh receiving context. The registrar must persist its operation key for retry. Mounting conservatively adds the entire mounted space to exposure.
- `resolve(write=True)` selects a writable destination; it does **not** publish or perform a write. A03 attaches a read-only repository. Exact write attribution, response-enqueue fencing, source-flow policy and graph attachment are later tasks.
- Quiesce external writers for owner migration and file replacement. For restore, first commit `begin_restore`, then supply the verified replacement to `restore_store`. This is explicit rebind, not an atomic filesystem restore or proof of power-loss durability. The simulated crash is a transaction-boundary exception followed by reopen, not a machine crash.
- Legacy migration creates a verified pre-upgrade backup in the catalog directory; retries before migration can leave additional backups for owner review. This task does not implement backup retention, multi-catalog coordination, cloning populated identities, or defense against arbitrary OS-owner file manipulation.
- No full-suite, installed-wheel, or later-stage acceptance claim. No live stores or external providers used; no project commit, push or merge.
