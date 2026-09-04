# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); version numbers
follow [SemVer](https://semver.org/spec/v2.0.0.html); dates in ISO 8601.

## [Unreleased]

### Added

- MCP tool safety annotations (`readOnlyHint`, `destructiveHint`,
  `openWorldHint`) and strict JSON argument validation.
- Read-only `notifications` CLI audit command with status/limit filters and
  goal filtering and opt-in payload output; the MCP notification tool remains
  payload-redacted by default.
- LHGP dual-track entrypoint polish (`lhgp --version`, `lhgp-mcp`) and updated
  plugin/roadmap documentation.
- Release artifacts now validate embedded plugin metadata (strict SemVer,
  `lhgp` identity, and canonical `lhgp-mcp` command) during CI.
- Canonical `lhgp` Python namespace facades now cover the runtime layers,
  while legacy `longtask` imports remain compatible during migration.
- Persistence schema helpers and notification outbox exports are now explicit
  under `lhgp.persistence`, with lazy loading preserved for legacy import order.
- Canonical RPC and persistence package APIs now expose stable protocol,
  schema, event, notification, and executor-side entry points with lazy
  loading where legacy import order requires it.
- `contract/get` now exposes contract-scoped `decision_history` and
  `attempt_history`, with bounded `decision_limit` / `attempt_limit` controls
  across RPC, CLI, MCP, and model skills.

### Fixed

- Detached daemon and subprocess test lifecycle cleanup, including parent
  pipe closure after child exit.
- MCP canonical aliases now retain their safety annotations and report the
  correct server identity when launched through `lhgp-mcp`.
- Schema startup reconciliation now repairs partially upgraded v2 databases,
  including missing external-handle capability columns, before daemon scans.
- Doctor output and README quickstart commands now use the canonical LHGP name.
- Claims evidence is re-anchored to the latest verified implementation commit;
  the full 7-gate suite remains green with 557 tests at 81.50% coverage.
- Unavailable L1 wakeup channels now emit one deduplicated `wakeup/degraded`
  event per daemon lifecycle instead of writing noise on every tick.
- Failed wakeup task disarms remain tracked and are retried on the next daemon
  tick, preventing stale OS scheduler entries after transient errors.
- dogfood v5 now preflights `request-verification` for blocked deliveries and
  falls back to a revision contract only after an explicit protocol refusal.

## [0.1.0a0] - 2026-09-01

### Added — Developer Preview release

The reference implementation is now feature-complete for the scope declared
in [DESIGN.md v0.7](DESIGN.md). All 7 quality gates (`format / lint / arch /
deps / claims / typecheck / test+coverage`) pass on Windows; CI matrix
expands to ubuntu + macOS.

#### Protocol surface (DESIGN §5, §11.1)

- 24 JSON-RPC methods over the control plane, dispatched through
  `src/longtask/rpc/handlers.py` + `src/longtask/rpc/executor_api.py`.
- MCP server thin layer (`longtask-mcp` script entry, `src/longtask/mcp_server.py`)
  exposing core `longtask_*` tools plus LHGP aliases and audit/control
  extensions to any MCP-compatible agent harness.
- §17 `longtask-contract` skill (`skills/longtask-contract/SKILL.md`) teaching
  models how to draft contracts, write handovers, and avoid common pitfalls.

#### Daemon lifecycle (DESIGN §3.3, §15.2)

- `longtaskd` start / stop over a real detached subprocess (Windows:
  `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, POSIX: `start_new_session`).
- Graceful stop via `daemon.stop` flag, hard kill only on grace expiry.
- `longtask-mcp` (stdio JSON-RPC 2.0) ships as the model-facing companion.

#### Attempt lifecycle (DESIGN §3.4, §5.1, §5.2, §7, §10)

- `AttemptRunner` (`src/longtask/cli/runner.py`) drives `start_attempt` +
  `poll_attempts`: prepare re-validation, spawn, heartbeat lease renewal,
  terminal collect, and stale marking.
- Dead-lease reclaim path on redispatch (§7): `reclaim_lease` not bare
  `acquire_lease`.
- §5.2 cross-check verifier: `_dispatch_verifier` auto-derives a verifier
  attempt (`role: VERIFIER`) with a candidate ≠ executor; the
  `_judge_verifier_outcomes` tick-end hook then promotes
  `contract.completed` on verifier `attempt/succeeded` or sends the
  contract back to `ACTIVE` on `attempt/failed`.

#### Ephemeral context (DESIGN §4.1)

- `compile_context_snapshot` materializes `context/attempts/<id>/active.md`
  and `scratch.md` per attempt; the `ContextPolicy` parses
  `contract.context.{required,max_bytes,expires_after_minutes}`.
- Capacity contract is fail-closed: overflow raises `CapacityRefusedError`
  and persists `context/capacity-refused` before refusing attempt start.
- Handover digest is auto-merged into `task_prompt` so re-dispatched
  attempts inherit verification-failure context (closes the
  v1 real-run "second attempt wasted" gap documented in
  `examples/agent-cli-model-provider-run/`).

#### Persistence, budget, contracts

- Budget hardness (§6.3) via `attempt/started` event count, not a static
  draft field. `max_dispatches` consumed on every successful dispatch;
  exhausted → tier 5 `blocked(need-user)`.
- Frozen-zone immutability enforced on `patch`; only `soft_guidance` /
  `acceptance` / `workload_estimate` are mutable.
- `request_id` idempotency on every state-mutating RPC (§11.3).

#### Executor-side RPC (DESIGN §11.2)

- `attempt/status` returns the attempt's event history + lease posture.
- `lease/renew` renews with fencing (lease gen / holder match); mismatches
  return `LEASE_FENCED`.
- `attempt/write-back` writes progress + attempt state with the same
  fencing; `request_id` is honored for retry semantics.

#### Layered wakeup (DESIGN §6.4, ADR-0002)

- L0 power guard via `SetThreadExecutionState` on Windows, port-injectable.
- L1 plan-task registration with `max(next_wakeup, deadline - margin)`.
- New event types: `wakeup/sleep-guard`, `wakeup/rtc-armed`,
  `wakeup/rtc-fired`, `wakeup/degraded`.
- L2 / L3 require external infrastructure and remain `accepted_debt` (see
  `quality/claims.json#strict-deadline-wakeup-design`).

### Known limitations (accepted debt, not blockers for v0.1.0a0)

- L2 / L3 wakeup not implemented (cloud + relay, external infra).
- Headless harness subprocess lifetime vs. `Popen` handle alignment
  (real-run example in `examples/agent-cli-model-provider-run-v2/`).
- `control/spawn` is vocabulary-only; external RPC to `AttemptRunner`
  not yet exposed.
- agent-cli / Claude Desktop etc. each need their own `longtask-mcp` registration
  in their MCP client config — out of protocol scope.

### Documentation & examples

- `examples/agent-cli-model-provider-run/` — v1 real execution: workspace artifacts
  delivered by `model-provider-M2.7-highspeed`; cross-check caught a wrong
  unit-test assertion.
- `examples/agent-cli-model-provider-run-v2/` — same task with §4.1 context + §5.2
  verifier enabled; full event chain `contract/prepared → approved →
  started → context/snapshot → attempt/succeeded → contract/completed`.
- `examples/mcp-discovery/` — MCP e2e trace (8-step contract lifecycle
  via `longtask-mcp` stdio).

### Internal (commit-level highlights)

- `911862d` — §4.1 ephemeral context
- `2b34348` — executor-side RPC
- `84f0b46` — §5.2 verifier auto-dispatch
- `7405173` — §17 skill
- `79c0f78` — MCP server thin layer
- `5 prior commits` (daemon lifecycle, layered wakeup, end-to-end runs)
