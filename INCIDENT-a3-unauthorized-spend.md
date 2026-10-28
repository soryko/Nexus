# Incident — unauthorised paid execution, 2026-09-20

**Two A3-shaped arm-runs reached the real model endpoint and consumed 2 669 285 tokens.**
Paid execution was explicitly stopped. No approval existed for any A3 arm-run, and
[`LAUNCH-A3.md`](LAUNCH-A3.md) is unapproved.

I caused this while building the production rehearsal. It was not a sweep, not a launch, and
not authorised by anything.

## What was spent

| | tokens | |
| --- | ---: | --- |
| `k1 / attempt 1 / policy B` | 1 357 232 | input 25 478 · output 32 298 · cache-read 1 299 456 |
| `k1 / attempt 1 / policy A` | 1 312 053 | input 25 597 · output 26 424 · cache-read 1 260 032 |
| **total** | **2 669 285** | |

Both hit `max_turns` at the 45-response ceiling, 46 envelope turns each, and both wrote real
patches (9 218 B and 5 259 B). The CLI reported 1.445661 and 1.595963 USD; **that field has
no provenance on this model** (`runner-a1` §3) and is recorded here only because it was
reported. The tokens are the measurement.

## What happened

`rehearse_a3_production.py` starts a loopback stub and a forwarder pointed at it, then runs
the production adapter. It checked that *something* was listening on the forwarder port and
treated that as success.

A `model_forwarder.py` had been running on port **8899 since 2026-09-15 08:54**, started
without `A1_FORWARDER_STUB_UPSTREAM` — so pointed at the **real** upstream. The forwarder my
rehearsal started could not bind that port, exited, and was never checked. The arms reached
the five-day-old forwarder instead, with a real `DEEPSEEK_API_KEY` inherited from the
environment.

**Four defects, each of which alone would have prevented it.**

1. **The rehearsal used the production port.** 8899 is the configured forwarder port, so the
   one port guaranteed to be occupied is the one it chose.
2. **"Something is listening" was read as "my process is listening."** The check could not
   distinguish my stub from anything else that answers on that port.
3. **The process it started was never checked for having died.** A `Popen` that exits
   immediately looked identical to one serving traffic.
4. **`setdefault` left a real API key in the child environment.** Reaching a real forwarder
   was necessary for this to be billable; an inherited real key is what made it so.

And one more, which is why it was not caught earlier: **the stub's request log is only
written on SIGTERM**, so there was nothing on disk to check mid-run, and the rehearsal
verified the prompts only *after* the run — by which time the spend had happened.

## What was NOT the cause

The sandbox held. The boundary controls were green on prepared arms with
`require=isolation.REQUIRE_A3`, the environment gate passed 8 checks, and the arms were
confined exactly as designed. **The boundary is a boundary on what an arm can read and
reach, not a budget.** Nothing in the isolation design is meant to stop a correctly
sandboxed arm from talking to the one endpoint it is given, and it did not fail here.

## The evidence is kept

`/Users/soko/Cerebros/nexus-a1-fixtures/a3-unauthorized-20260920/` — both records, both
traces, both patches, the sandbox profiles and the environment-gate output, as written.

**These two arm-runs are not A3 results and may not be used as any.** They ran under
`a3-v1` against `k1/attempt1`, which is a registered A3 cell, and reusing them would launder
unauthorised spend into the experiment — and they are also, separately, a sample of one cell
selected by an accident. If A3 is ever approved it starts from nothing.

They are also **not netted against the 30 000 000 proposal**, in either direction. That
threshold governs an authorised sweep; this was not one.

## What is closed

`rehearse_a3_production.py` now:

- uses a **private forwarder port (8918)** and refuses if either port is already in use;
- **binds** rather than probes — a listener it did not start is a refusal;
- checks the processes it started are **still alive**;
- **overwrites** `DEEPSEEK_API_KEY` with a stub value rather than inheriting one;
- runs a **positive upstream control before any arm-run**: the stub echoes a per-run instance
  token, the rehearsal sends one request through the forwarder, and it refuses unless that
  token comes back. A forwarder pointed anywhere else cannot produce it;
- drives the runner with a **rehearsal configuration written into the scratch**
  (`a3-v1-REHEARSAL`), because `forwarder_port` comes from the config and an environment
  variable would have changed nothing. `run_a3.py` prints that no row produced under it is
  an A3 result.

Verified after the repair: the same rehearsal consumes **108 tokens**, both registered prompt
digests are observed outbound, and the production writer produces every artifact the reporter
consumes.

## What this says about the design

The stop conditions in the registration govern a sweep that is *running*. They had nothing to
say about a development script that reaches a paid endpoint by accident, because the
authorisation model assumes spending happens inside a sweep. **It does not.** Any tool that
can invoke the runner can spend, and the only reliable control is a positive one at the
egress — proving where the traffic goes before any of it is sent, not counting tokens after.
