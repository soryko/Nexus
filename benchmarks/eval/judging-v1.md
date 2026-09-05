# Nexus retrieval benchmark v1 — judging sheet

**Nothing here has been judged yet, and no ranking work has been done.** Judgments
freeze this benchmark; correcting a label afterwards requires a new version, never an
edit in place.

## How to judge

Read the items once, then work through the queries. For each query, list the item IDs
you consider `2` and those you consider `1`. Everything unlisted is `0`.

| Grade | Meaning |
| --- | --- |
| `2` | Directly answers the information need |
| `1` | Useful supporting evidence, but does not answer it |
| `0` | Irrelevant |

Add a short rationale per query, and say explicitly where you are uncertain — an
uncertain label that is marked as such is far more useful than a confident guess.

Judge the **content against the information need**, not what any system could retrieve.
Some needs may have no answer here at all; leaving a query with no items listed is a
valid and expected outcome.

Where an item is marked as an earlier version of another, decide for yourself whether
being superseded makes it irrelevant, still useful as background, or exactly what the
need asks for.

## Items (23)

**i01** · `observation` · tags: payments

> Settlement files from the acquirer arrive between 02:10 and 02:40 UTC. Anything later than 03:00 has always meant an upstream incident, not a slow transfer.

**i02** · `decision` · tags: database

> The ledger is append-only. Corrections are new compensating entries, never updates or deletes of an existing row.

**i03** · `decision` · tags: auth · _replaces i04_

> Service-to-service authentication uses short-lived OIDC tokens from the platform issuer. Tokens live for ten minutes and are refreshed by the sidecar.

**i04** · `decision` · tags: auth · _earlier version of i03_

> Service-to-service authentication uses Kerberos tickets issued by the internal KDC.

**i05** · `decision` · tags: payments, retry · _replaces i11_

> Retry policy for card charges: five attempts with exponential backoff starting at 400ms. The acquirer raised the per-merchant rate limit in March, so the earlier three-attempt cap is no longer required.

**i06** · `procedure` · tags: database

> To add a column safely: ship the migration alone, deploy, verify replication lag under one second, then ship the backfill as a separate change.

**i07** · `procedure` · tags: testing

> Before merging anything that touches money movement, run the full suite twice with different random seeds. Ordering bugs in the ledger only show up on one of the two runs.

**i08** · `observation` · tags: payments

> Chargeback volume roughly doubles in the two weeks after a promotional campaign. Support staffing has never been adjusted for it.

**i09** · `constraint` · tags: payments, compliance

> Refunds above 10,000 in any currency require a second approver recorded in the audit trail before the transfer is submitted.

**i10** · `failure` · tags: ci, retry

> The CI job for the search package retries flaky browser tests up to three times. This masked a genuine race in the session store for two weeks before anyone noticed.

**i11** · `decision` · tags: payments, retry · _earlier version of i05_

> Retry policy for card charges: three attempts, 2s fixed backoff. Chosen because the acquirer rate-limits above five requests per second per merchant.

**i12** · `procedure` · tags: deploy · _replaces i23_

> Deploys are continuous: every merge to main that passes the gate ships automatically. There is no release call and no fixed deploy window.

**i13** · `constraint` · tags: auth

> Never log a bearer token, even truncated. A truncated token is still enough to correlate a session across services.

**i14** · `observation` · tags: search

> The product search endpoint retries the upstream index twice on timeout. Latency at p99 is dominated by those retries, not by the index itself.

**i15** · `failure` · tags: payments

> A duplicate charge incident in January came from a client generating a fresh idempotency key on every retry attempt. The fix was client-side; the server behaved as specified.

**i16** · `constraint` · tags: payments, compliance

> Card PANs must never be written to application logs, including at debug level. The redaction filter in payments/logging.py is the only sanctioned path.

**i17** · `decision` · tags: observability

> Structured logs use one event per request with a correlation id. Nested spans were tried and dropped: the volume tripled and nobody queried the extra depth.

**i18** · `decision` · tags: payments, idempotency

> Charge requests carry a client-supplied idempotency key. A repeat of the same key with the same body returns the original receipt; the same key with a different body is rejected rather than merged.

**i19** · `observation` · tags: infrastructure

> The payment terminal fleet in retail stores runs a separate firmware release train. Nothing in this repository deploys to those terminals.

**i20** · `failure` · tags: deploy, database

> The June outage was a migration that added a NOT NULL column with a default to a 40 million row table, holding the writer for eleven minutes.

**i21** · `procedure` · tags: payments, testing

> Run the payments integration suite after changing retry behaviour: `pytest tests/integration/payments -k retry`. It exercises the acquirer sandbox and takes about four minutes.

**i22** · `constraint` · tags: database

> Never run a schema migration and a backfill in the same deploy. The backfill holds the writer long enough that the health check fails and the deploy rolls back mid-migration.

**i23** · `procedure` · tags: deploy · _earlier version of i12_

> Deploys go out manually on Tuesdays and Thursdays after the release call.

## Queries (14)

### q01 — "how many times do we re-attempt a failed card charge"

Information need: Find the current retry policy for card charges.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q02 — "retry policy"

Information need: Find memories about retry behaviour; several unrelated senses of retry exist in the corpus.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q03 — "payments integration suite"

Information need: Find the procedure for testing after a retry change.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q04 — "kerberos"

Information need: Find how service-to-service authentication works.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q05 — "why did the retry cap change from three attempts"

Information need: Find the reason the earlier three-attempt policy was replaced.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q06 — "idempotency key"

Information need: Find the idempotency contract and any related incident.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q07 — "what is our database backup schedule"

Information need: Find how and how often the database is backed up.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q08 — "migration backfill same deploy"

Information need: Find the constraint and procedure about migrations and backfills.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q09 — "never write secrets to logs"

Information need: Find constraints about logging sensitive values. Neither memory uses the word secrets.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q10 — "when do deploys happen"

Information need: Find the current deploy cadence.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q11 — "payment terminal firmware"

Information need: Find anything about terminal firmware; shares the word payment with much of the corpus.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q12 — "who approves large refunds"

Information need: Find the refund approval constraint.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q13 — "append only ledger"

Information need: Find the ledger design decision.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 

### q14 — "how do we roll back a failed canary"

Information need: Find the procedure for rolling back a canary deploy that is failing.

- grade 2: 
- grade 1: 
- rationale: 
- uncertainty: 
