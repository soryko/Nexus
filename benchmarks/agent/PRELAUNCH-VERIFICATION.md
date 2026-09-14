# Pre-launch verification — A2 calibration

Produced by `make-verification.sh` against the exact revision below. Every line is the
output of a command that was run, not a summary of one. No model was invoked.

```
revision           efb42b39e9b23e8adce50415289ab549cd8e2b1a
branch             docs/a2-calibration
date (UTC)         2026-09-14T10:56:07Z
config version     calib-v2
schedule digest    0a352b82ced14f10
```

## Accounting and driver counterexamples
Both driver paths are exercised with a stub runner: normal completion and the budget stop.
```
15/15 counterexample groups pass
```

## Write-detector counterexamples
```
17/17 counterexamples pass
```

## Arm environment gate, against a prepared arm
```
  [PASS] interpreter imports the intended checkout  click.__file__=/Users/soko/Cerebros/nexus-a1-fixtures/calib-run/c30/run-k1/attem
  [PASS] documented test command executes a test    rc=0 passed=23 failures_or_errors=False :: 23 passed in 0.03s
  [PASS] here-document works                        ['HEREDOC-OK']
  [PASS] scratch file round-trips in the checkout   ['SCRATCH-OK']
  [PASS] egress denied (paired control)             blocked_inside=True works_outside=True

ALL CHECKS PASS
```

## Isolation: heredocs work, and are arm-private
A sentinel written through one arm's profile, read through another's.
```
heredoc_works: True
heredoc_tail: ['heredoc-ok']
tmpprefix_set: True
cross_arm_read_blocked: True
cross_arm_tail: ['cat: /var/folders/24/01628ttx16j2tszb0lxfs0jw0000gn/T/tmpfkmyhtpk/baseline/tmp/zsh_sentinel: Operation not permitted']
```

## Product suite
```
SKIPPED [1] tests/core/test_b2a_mutations.py:138: development run: set NEXUS_MUTATION_MATRIX=1
SKIPPED [1] tests/core/test_b2b_mutations.py:410: development run: set NEXUS_MUTATION_MATRIX=1
334 passed, 2 skipped in 31.21s
```
