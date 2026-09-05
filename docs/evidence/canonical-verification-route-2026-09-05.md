# Canonical verification command evidence (2026-09-05)

## Finding

The `contract/request-verification` handler had been registered in the legacy
`longtask` RPC table but not in the canonical `lhgp` table.  Consequently the
documented CLI command returned `STATE_FORBIDDEN: method not implemented`, even
though the MCP/legacy handler and its tests were green.

## Fix

Commit `060168c` registers `CONTRACT_REQUEST_VERIFICATION` in the canonical
handler table.  The same table now also exposes the previously omitted Goal
read/advance methods (`goal/get`, `goal/list`, `goal/update`, `goal/advance`,
`goal/next`, and `goal/contract-draft`).  A regression test exercises the
canonical route against a real SQLite store.

## Runtime evidence

Against the fresh `.dogfood-v5` ledger:

```text
lhgp --data-dir .dogfood-v5 request-verification lt-dogfood-v05
→ verification_requested: true

daemon events:
verification/requested
verification/consumed (outcome=dispatched)
verification/started

attempts:
ver-20260905011710--v05  role=verifier  executor=dsh-verifier  state=running
```

No new executor attempt was created by the request.  The daemon and the
detached verifier process were stopped after capture.

## Verification gates

- `tests/integration/test_request_verification.py`: 13 passed
- Full quality gate: 7/7 gates passed
- Full suite: 630 passed
- Coverage: 82.15%
- Typecheck: 162 source files, no issues
