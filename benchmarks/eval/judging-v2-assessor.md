# Nexus retrieval benchmark v2 — judging sheet

## Items (25)

*(unchanged)*

## Queries (17)

### q03 — "payments integration suite"

Information need: Find the procedure for testing after a retry change.

- grade 2: i10
- grade 1: i23
- rationale: i10 is exactly the procedure asked for — it names the retry-change trigger, the command, and what it exercises. i23 is a testing procedure for money-movement changes (run the suite twice with different seeds), which would plausibly also apply after a retry change to payments, but it is about ordering bugs in the ledger, not about retry changes, so it is supporting rather than answering.
- uncertainty: Whether i23 deserves 1 or 0 depends on reading "after a retry change" as implying money movement. A retry change in payments touches money movement, so I lean 1, but a stricter reading makes it 0. i20/i16 (the retry policy itself) are context for what changed, not testing procedure, so I left them at 0.

### q11 — "payment terminal firmware"

Information need: Who is responsible for payment terminal firmware releases?

- grade 2: 
- grade 1: i18
- rationale: Nothing here names an owner, team, or person for terminal firmware. i18 is the only item on the subject and it answers a neighbouring question — that firmware ships on a separate release train and nothing in this repository deploys to the terminals — which narrows responsibility by exclusion without naming who holds it.
- uncertainty: A lenient assessor could call i18 a 2 on the grounds that "a separate firmware release train, not us" is the practical answer to a "who owns this" question. I judged the need literally: it asks who, and no item says who.

### q06 — "idempotency key"

Information need: How should callers reuse idempotency keys, and what can go wrong?

- grade 2: i04, i15
- rationale: The need has two halves and each item answers one. i04 gives the caller-facing contract: same key plus same body returns the original receipt, same key plus a different body is rejected rather than merged. i15 gives the failure mode: a client minting a fresh key per retry attempt caused duplicate charges, and the defect was client-side. Together they are the complete answer, and each is directly responsive on its own half.
- uncertainty: None material. i20/i16 are about retry counts, not keys, and I kept them at 0 even though retries are the setting in which the i15 mistake occurs — a retry policy does not tell a caller anything about key reuse.

### q02 — "retry policy"

Information need: Find what retry behaviour exists across the system.

- grade 2: i20, i25, i12
- grade 1: i16, i10, i15, i07
- rationale: This is a survey need — it asks for the retry behaviour that exists, plural, not one policy. Three distinct retry mechanisms are described in the collection and each is a direct answer: i20 (card charges, five attempts with exponential backoff), i25 (product search retries the upstream index twice on timeout), i12 (CI retries flaky browser tests up to three times). i16 is the superseded card policy; for a survey of what exists now it is background, so 1 rather than 2. i10 documents that retry behaviour has a dedicated test suite, i15 shows retry behaviour interacting with idempotency, and i07 is arguably a retry-adjacent timing expectation — all supporting.
- uncertainty: i07 is the weakest of the 1s; it is about when settlement files arrive and what lateness means, and "slow transfer" only gestures at retry. I would not object to 0 there. I also considered demoting i12 to 1 since it is filed as a failure rather than a policy, but it states the actual retry configuration of a real job, which is what the need asks for. Whether i16 should be 0 instead of 1 depends on whether "exists" means "is currently in force"; I read superseded policy as still useful context for a survey.

### q01 — "how many times do we re-attempt a failed card charge"

Information need: Find the current retry policy for card charges.

- grade 2: i20
- grade 1: i16
- rationale: i20 is the current policy and answers the count directly: five attempts, exponential backoff from 400ms. i16 states three attempts with 2s fixed backoff and is explicitly replaced by i20, so it is the wrong answer to "current" — but it carries the rate-limit rationale that explains the shape of the policy, so it is useful background rather than irrelevant.
- uncertainty: The line between 1 and 0 for i16 is the real judgment call. A superseded policy is actively misleading if a reader stops there, which argues for 0; I settled on 1 because i20 names and supersedes it explicitly, so the pair reads correctly together and i16 alone is unlikely to be encountered without it. i25 and i12 are retries elsewhere in the system, not card charges, so 0.

### q09 — "never write secrets to logs"

Information need: What are the rules about writing sensitive values to logs?

- grade 2: i21, i08
- grade 1: i14
- rationale: Both 2s are stated rules about sensitive values in logs, and the need is plural. i21 covers card PANs, including at debug level, and names the sanctioned redaction path. i08 covers bearer tokens, including truncated ones, with the reason. i14 is the logging design decision (one event per request with a correlation id) — it defines the logging substrate the rules apply to but states no rule about sensitive values.
- uncertainty: i14 is a borderline 1/0. A correlation id is the non-sensitive alternative to logging a session identifier, which is close to i08's concern, but that connection is mine, not the item's. I would accept 0.

### q15 — "what was the deploy schedule before it changed"

Information need: Find the deploy cadence that was in effect before the current one.

- grade 2: i22
- grade 1: i09
- rationale: This need asks specifically for the superseded state, so being superseded is precisely what makes i22 the right answer: manual deploys on Tuesdays and Thursdays after the release call. i09 is the current cadence and is supporting — it establishes that i22 is the prior one and what replaced it — but it does not itself state the earlier schedule.
- uncertainty: None on i22. The i09 label depends on whether a "before" question needs the "after" for orientation; I say it helps but does not answer.

### q05 — "why did the retry cap change from three attempts"

Information need: Find the reason the earlier three-attempt policy was replaced.

- grade 2: i20
- grade 1: i16
- rationale: i20 carries the reason in its own text — the acquirer raised the per-merchant rate limit in March, so the three-attempt cap is no longer required. That is the whole answer. i16 supplies the other half of the picture: the three-attempt cap existed because the acquirer rate-limited above five requests per second per merchant, which is what makes the raised limit meaningful. It states the prior constraint but not the reason for the change.
- uncertainty: i16 could be argued to 2 on the view that a "why did X change" need is only fully served by both the old rationale and the new one. I kept it at 1 because the change reason is stated entirely within i20.

### q17 — "how many times does search retry the index"

Information need: Find the retry behaviour of the product search request path.

- grade 2: i25
- grade 1: 
- rationale: i25 answers directly: the product search endpoint retries the upstream index twice on timeout, and those retries dominate p99 latency. Nothing else in the collection concerns the search request path.
- uncertainty: i12 is tagged ci and retry and mentions the search package plus a race in the session store, so it shares vocabulary with this need. I judged it 0 because it is about CI retrying browser tests, not about the search request path retrying the index — a different retry, in a different layer. This is the one place in the sheet where a term-matching reading and a content reading disagree sharply, and I went with content.

### q14 — "how do we roll back a failed canary"

Information need: Find the procedure for rolling back a canary deploy that is failing.

- grade 2: 
- grade 1: 
- rationale: No item describes canary deploys or a rollback procedure. i09 says every passing merge ships automatically, which does not describe canaries or rollback. i03 and i17 both mention a deploy rolling back, but as the consequence of a failed health check during a migration, not as a procedure anyone follows, and neither involves a canary. I judged this need to have no answer here.
- uncertainty: If an assessor treated i17's "the health check fails and the deploy rolls back mid-migration" as evidence about how rollback works mechanically, it would be a weak 1. I declined because the need asks for a procedure for canaries specifically, and stretching to the automatic rollback of a migration answers a different question.

### q10 — "when do deploys happen"

Information need: Find the current deploy cadence.

- grade 2: i09
- grade 1: i22
- rationale: i09 is the current cadence and answers directly: continuous, every merge to main that passes the gate, no release call and no deploy window. i22 is the replaced schedule — wrong as an answer to "current", useful as background on what the cadence used to be.
- uncertainty: Same 1-versus-0 tension as i16 on q01. i22 read alone would mislead someone asking when deploys happen today; I gave it 1 on the same reasoning, that i09 explicitly negates it ("no release call") and the pair reads correctly together.

### q12 — "who approves large refunds"

Information need: Find the refund approval constraint.

- grade 2: i06
- grade 1: 
- rationale: i06 is the constraint: refunds above 10,000 in any currency need a second approver recorded in the audit trail before the transfer is submitted. Nothing else addresses refund approval.
- uncertainty: The need asks "who", and i06 says "a second approver" without naming a role or team. I still call it 2 — the constraint is the thing being asked for, and the phrasing of the information need is "find the refund approval constraint", which i06 is exactly.

### q13 — "append only ledger"

Information need: Find the ledger design decision.

- grade 2: i19
- grade 1: i23
- rationale: i19 is the decision: the ledger is append-only, corrections are compensating entries, never updates or deletes. i23 is a testing procedure that depends on ledger behaviour (ordering bugs in the ledger showing up on only one of two seeded runs) — related to the ledger but not a design decision about it.
- uncertainty: i23 is a marginal 1; it tells you nothing about the design itself, only that ledger ordering is fragile under test. 0 would be defensible. i02/i11 compare settlement files against ledger entries but describe a reconciliation job, not ledger design, so 0.

### q04 — "kerberos"

Information need: Find how service-to-service authentication works.

- grade 2: i05
- grade 1: i01, i08
- rationale: The query term is Kerberos but the stated need is how service-to-service auth works, and the need governs. i05 is the current mechanism: short-lived OIDC tokens from the platform issuer, ten-minute lifetime, refreshed by the sidecar. i01 is the Kerberos/KDC arrangement that i05 replaced — it matches the query string exactly while being the outdated answer, so it is background, not the answer. i08 is a rule about handling the bearer tokens the current scheme issues, which is supporting evidence about how the scheme operates in practice.
- uncertainty: i01 is the sharpest superseded-item call on the sheet, because the query literally is its distinguishing term. I judged content against the need rather than against the query string: someone asking how service auth works today is served wrongly by i01. If the need had been "what did we use before OIDC", i01 would be the 2. i08's 1 is softer — it constrains logging rather than describing the auth flow.

### q16 — "nightflow"

Information need: Find what the overnight reconciliation job does and when it reports.

- grade 2: i11
- grade 1: i02, i07
- rationale: The need asks what the job does and when it reports, and both items give the same answer for that: compares acquirer settlement files against ledger entries, files a discrepancy report by 06:00 UTC. They differ only in the job's name. i11 is current (sweepcheck) and so is the 2; i02 (nightflow) matches the query term but carries a name that is no longer right, which is the one substantive thing that changed between them — so it is background. i07 supports by giving the arrival window of the settlement files the job consumes, which bounds when it can run.
- uncertainty: i02 is a genuinely hard call and I want to flag it rather than smooth it over. Every fact the need asks for is present and correct in i02; only the name is stale, and the name is what the query asked by. An assessor could reasonably grade i02 a 2 on the grounds that it answers both halves of the stated need accurately. I put it at 1 because a reader who takes the job name away from i02 will look for a job that no longer exists under that name.

### q07 — "what is our database backup schedule"

Information need: Find how and how often the database is backed up.

- grade 2: 
- grade 1: 
- rationale: Nothing in the collection concerns backups, snapshots, restore, or retention. The database items cover migration safety (i03, i17, i24) and ledger design (i19); i24 mentions verifying replication lag, which is replication, not backup. No answer here.
- uncertainty: None. I considered whether replication in i24 counts as supporting evidence for durability and concluded it does not — replication lag is a deploy-safety check in that item, and treating it as backup evidence would be stretching.

### q08 — "migration backfill same deploy"

Information need: How should we deploy a schema change that requires a backfill?

- grade 2: i24, i17
- grade 1: i03
- rationale: The need is prescriptive, and the two 2s answer it from both directions. i24 is the positive procedure: ship the migration alone, deploy, verify replication lag under one second, then ship the backfill separately. i17 is the prohibition and its reason: never in the same deploy, because the backfill holds the writer until the health check fails and the deploy rolls back mid-migration. i03 is the incident that motivates the rule — a NOT NULL column with a default on a 40 million row table holding the writer for eleven minutes — which is evidence for why, not instruction on how.
- uncertainty: i03 could be argued to 2 if the need is read as "what happens if we get this wrong", but as stated the need asks how to deploy, and i03 gives no procedure. I am comfortable with 1.
