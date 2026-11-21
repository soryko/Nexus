# Owner administration (Stage A)

`nexus-memory-admin` operates on one explicitly named catalog. Run it only in an owner-controlled directory. Do not use an agent to choose a catalog, store path, checkout, receiving context or approval preview. There is no default store. Every mutation requires a stable `--operation-key`; retry the original command with that key. A changed command is a new decision, not a retry. Successful stdout is a JSON receipt; a rejected request returns nonzero. Neither type of grant prints its credential.

## A disposable walkthrough

The following shell example requires `jq`. It creates its own private directory and uses only IDs from receipts. Nothing here needs a real checkout or profile.

```sh
set -eu
umask 077
work=$(mktemp -d); cd "$work"
admin() { nexus-memory-admin --catalog "$work/catalog" "$@"; }
admin catalog init --operation-key init
printf '%s\n' '{"name":"Example","kind":"research"}' > create.json
admin space create --request create.json --operation-key create > space.json
sid=$(jq -r .space_id space.json)
jq '{store_id}' space.json > migrate.json
admin store migrate --request migrate.json --operation-key graph-schema
admin space list

printf '%s\n' '{"entities":[{"temp_id":"a","kind":"symbol","qualified_key":"example"}],"assertions":[{"subject":{"kind":"entity","entity_id":"a"},"predicate":"CALLS","object":{"kind":"entity","entity_id":"a"}}]}' > batch.json
approve() {
  jq -n --arg s "$sid" --arg family "graph $1" --arg key "$2" --slurpfile draft "$3" \
    '{family:$family,operation_key:$key,space_id:$s,exposed_space_ids:[$s],request:$draft[0]}' > preview.json
  admin grant issue --request preview.json --operation-key "issue-$2" --out "$2.grant" > "$2.approval.json"
}
approve curate add batch.json
admin graph curate --space-id "$sid" --request batch.json --operation-key add --grant-file add.grant > graph.json
jq '{operation_id,assertion_revisions}' graph.json

# Correct exactly the revision just returned. Another writer changing the head
# produces revision_conflict instead of silently overwriting its work.
jq '.assertion_revisions[0] as $r | (.id_mappings.a | {kind:"entity",entity_id:.}) as $e |
    {assertion_id:$r.assertion_id,expected_revision_id:$r.revision_id,
     replacement:{subject:$e,predicate:"DEPENDS_ON",object:$e}}' graph.json > edit.json
approve correct edit edit.json
admin graph correct --space-id "$sid" --request edit.json --operation-key edit --grant-file edit.grant
jq '{grant_id}' edit.approval.json > revoke.json
admin grant revoke --operation-key revoke-edit --request revoke.json
```

A manual curation is **not an exact save**. It has a separate operation ID, graph event sequence, revision list and temporary-ID mappings. A memory evidence input names the exact space, memory and revision, and a byte span with its SHA-256. An assertion correction names its expected revision; an entity resolution uses `EntityResolutionInput` with `expected_generation`. A stale operation cannot partially install new evidence or a new head. Owner curation sets human provenance and accepted review state; neither implies evidence or applicability.

## Other requests

* `space register`: `{"path":"/absolute/store.sqlite","expected_store_id":"…"}`. The ID check is optional. Quiesce the store. Legacy registration takes a verified online backup before migration and returns partition bindings. Do not register a copied UUID as another store. For a new project, `space create` accepts `kind:"project"` and `repo` (an absolute checkout); first enroll the main checkout with the existing project-scope launcher. Creation resolves that enrollment, checks the backlink for a linked worktree, and binds its shared Git identity before activation. Moving a main checkout does not transfer its enrollment to the new path.
* `store migrate`: use a **registered store ID**, not a locator. Quiesce the store during migration. The receipt reports the schema read back from the store and, for an upgrade, the verified backup path. The catalog binds the backup inode and content digest to the migration operation. An unexpected file, link, sidecar or unclaimed snapshot stops the upgrade; the CLI does not replace it. A crash after backup publication but before recording its identity leaves an unclaimed snapshot: preserve it and use a new operation key rather than treating it as verified. `store restore`: `{"store_id":"…","path":"/absolute/verified-snapshot.sqlite"}`. This rebinds the registered identity and partitions to the verified copy, invalidating old mounts; it does not overwrite the original file. Quiesce both stores during restore.
* `grant issue` with `{"space_id":"…","principal_id":"local","receiving_context":"host-owned-stable-id","mode":"read_only"}` delivers a private launcher ticket compatible with the existing broker. `read_write` is also available. Use the **same trusted context identity** for the same receiver. Do not accept it from model input. Revoke using the public `grant_id` from stdout, **not** the ticket. Never print, share, or version a grant file.
* Graph approval is a different grant. The issuer binds the catalog's stable writer stream, operation key, command family, destination, exposure set and complete JSON preview (including expected revisions). Only a single destination/audience is supported in Stage A. A new graph effect expires at 120 seconds. After a crash following destination commit, retry the identical operation: the destination receipt, not the catalog marker or clock, proves whether it committed. An expired approval cannot create a missing effect. Revocation denies further use.
* `proposal list/review` and `projection rebuild/status` advertise `unsupported_capability`. No Atlas owner UI, cross-space copy or later-stage workflow is implied. Inputs are JSON objects limited to 1 MiB. Use a fresh output filename; repeat issuance accepts only the identical owner-only mode-0600 credential. A failed command may leave an inert operation reservation for a safe retry.

## A10 verification receipt (base `e2e157b`)

Red: the two named administration tests ran and failed at setup importing the missing admin module (exit 1). Separate tests for enrolled project creation and resumable broker grants subsequently failed with `invalid_input` before their branches were implemented. Green: `uv run pytest -q tests/tools/test_spaces_admin.py tests/tools/test_project_scope.py tests/tools/test_backup_recipe.py` passed all 10 tests. The fault-injection check interrupts catalog bookkeeping after destination commit; a fake clock advances exactly 120 seconds, and the recovered receipt corresponds to one destination event. This simulates a process interruption, not disk loss or a power-cut durability test.

Review regressions were run before the fixes: seven cases failed (exit 1). The receipt fault left the approval revoked; unexpected snapshots and links were accepted, a sidecar produced the wrong error, a no-op upgrader claimed success, and a misplaced worktree marker was accepted. After the fixes the same named gate passed **17 tests** (exit 0). The revocation test checks rollback, the durable catalog receipt, and identical replay. Backup collision tests compare store bytes before and after refusal; the migration fault test retries against the recorded backup and checks the resulting store pragma. Checkout tests use only a temporary enrollment registry and synthetic Git worktrees.

Offline wheel check (exit 0): built `nexus_memory-0.1.0a3-py3-none-any.whl`, installed non-editably in a fresh venv, and ran outside the checkout with a temporary HOME. Isolated imports confirmed that both Nexus and the canonical project-scope helper loaded from that venv. The installed CLI initialized a catalog, created an enrolled project, migrated and replayed its receipt, issued a disposable approval to a private file, and replayed its revocation. No live data, credentials, deployment or full-suite result is part of this evidence.
