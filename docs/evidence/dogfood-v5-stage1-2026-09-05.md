# dogfood v5 stage-1 real run (2026-09-05)

## Scope

This run used the local DSH executor/verifier wrappers and a real detached
daemon.  It exercised the first Alpha break: the executor process was killed
without a graceful write-back, then the daemon recovered and re-dispatched.

## Observed result

- `att-20260905002710--v05` (`executor`, `dsh-headless`) was forcibly killed.
- The daemon observed the lost session and started
  `att-20260905002819--v05` with the same contract and handover context.
- The replacement executor reached `succeeded`.
- The daemon spawned independent verifier
  `ver-20260905002920--v05` (`dsh-verifier`, separate wrapper/model home).
- The verifier emitted a structured `lhgp-verdict` block; both acceptance checks
  passed, including the existing `charfreq.py` and its nine-test command run.
- The contract ended as `satisfied`, with `acceptance_status=passed` and
  `deadline_status=met`.
- Goal `lt-dogfood-v05` advanced from `stage-1` to `stage-2`.

The run was stopped cleanly with `lhgp --data-dir .dogfood-v5 stop`; no DSH
process remained afterward.  The complete authoritative event stream remains
in the local SQLite ledger (the runtime directory is intentionally ignored).

## Finding and remediation

The run exposed a real ordering race: a verifier had already succeeded, but
the scheduler still saw the contract as active until the end-of-tick verdict
hook and could start one extra executor.  The fix moves verifier outcome
judgement before dispatch decisions and adds a regression assertion that a
completed verifier yields zero new dispatches.  It is implemented in the
committed source and covered by the full quality gate.

## Evidence boundary

This proves the local single-host form of break-1 (session loss → daemon
recovery → independent verification).  It does not claim the stage-2 CLI
switch or daemon-restart break has been run yet.
