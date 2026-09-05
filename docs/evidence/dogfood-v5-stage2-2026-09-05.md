# dogfood v5 stage-2 real run (2026-09-05)

## Scope

This run exercised the Goal stage transition from stage-1 to a different
executor (`kimi-code`) while retaining an independent `dsh-verifier`.

## Observed result

- Goal `lt-dogfood-v05` was at `stage-2`; contract
  `lt-dogfood-v05-stage2` was admitted with explicit executor/verifier
  authority.
- Executor `att-20260905004159-age2` ran under `kimi-code` and exited
  successfully.
- The executor did not create the required `linecount.py` and
  `test_linecount.py`; its own output reported that the task details were not
  actionable.
- Independent verifier `ver-20260905004302-age2` inspected the workspace and
  failed both mandatory checks. The contract correctly remained
  `active/failed` rather than being marked complete.
- The daemon was stopped cleanly after the observation; no claim of stage-2
  completion is made.

## Finding and remediation

The run exposed a dogfood-driver flaw: its wait loop searched all attempts for
any succeeded verifier, so stage-1's old verifier could make stage-2 appear
finished. The driver now filters attempts by the stage-2 contract and gives the
stage-2 objective explicit artifact/function/test requirements. A future retry
will therefore wait for the correct verifier and provide a less ambiguous
handoff to the alternate CLI.

## Evidence boundary

This proves the protocol correctly refused to declare an unimplemented stage
complete and preserved the failed verification state. It does not prove that
the alternate CLI can finish stage-2 yet.
