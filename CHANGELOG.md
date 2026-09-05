# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); version numbers
follow [SemVer](https://semver.org/spec/v2.0.0.html); dates in ISO 8601.

## [0.1.0a0] - 2026-09-05

首次公开发布（Developer Preview）。内部开发者预览切割于 2026-09-01；
2026-09-05 完成产品定位统一、发布阻断项修复（B1/B2）与候选制品验证后发布。

### Changed

- Product name set to **限期合同中枢 / Deadline Contract Hub** (ADR-004 revision);
  "multi-agent task contract runtime" is retained as the category description only.
- Position LHGP as a multi-agent task contract runtime with an LLM-independent
  scheduling core; retain protocol names, package identity and compatibility.
- Align English/Chinese README and specification framing; distinguish multiple
  contracts and submitted-plan progression from optional future semantic planning.
- Replace the incomplete automatic-execution quickstart with an isolated,
  non-LLM contract lifecycle smoke. Add ADR-004, an executable release-first plan
  and an audit recording known blockers. This is documentation work, not a
  runtime fix or a new release.

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

- POSIX liveness probes now recognize zombie processes via `/proc` state and
  reap daemon children with `waitpid(WNOHANG)` before escalating a stop to
  SIGTERM, so daemon shutdown is graceful again on Linux instead of always
  being reported as forced.
- Linux process start times now come from `/proc/<pid>/stat` field 22 (stable
  after exit) instead of `st_ctime` (which changes when a process exits), and
  `reattach` binds provably-terminated runs whose pid has been fully reaped,
  per the documented branch-2 settlement semantics.
- CI runs the gate on macOS as a known-gap, non-blocking platform for this
  preview (the identity model has no `/proc` equivalent there yet).
- CLI and MCP `--help` no longer crash with `UnicodeEncodeError` on non-UTF-8
  consoles (e.g. cp1252); unencodable help characters are replaced.
- The `executor/health` integration test no longer depends on a
  machine-installed `codex` CLI (hermetic fixture uses the interpreter).
- mypy analysis is pinned to the Windows platform (the authoritative
  typecheck platform) so the three-OS CI matrix typechecks identically, and an
  unused Rust cache step was removed from CI.
- Timeout cancellation failures now orphan the attempt and retain its lease for
  reconcile grace/fencing instead of releasing execution control before the
  external process is known to have stopped.
- Verifier `attempt/started` events no longer consume the executor
  `max_dispatches` budget; execution and verification budgets remain separate.
- Detached daemon and subprocess test lifecycle cleanup, including parent
  pipe closure after child exit.
- MCP canonical aliases now retain their safety annotations and report the
  correct server identity when launched through `lhgp-mcp`.
- Schema startup reconciliation now repairs partially upgraded v2 databases,
  including missing external-handle capability columns, before daemon scans.
- Doctor output and README quickstart commands now use the canonical LHGP name.
- Claims evidence is re-anchored to the latest verified implementation commit;
  the full 7-gate suite remains green with 574 tests at 81.47% coverage.
- Unavailable L1 wakeup channels now emit one deduplicated `wakeup/degraded`
  event per daemon lifecycle instead of writing noise on every tick.
- Failed wakeup task disarms remain tracked and are retried on the next daemon
  tick, preventing stale OS scheduler entries after transient errors.
- dogfood v5 now preflights `request-verification` for blocked deliveries and
  falls back to a revision contract only after an explicit protocol refusal.
- Verification request consumption is now recorded by request event ID, so a
  terminal verifier cannot cause the same user request to be dispatched again.
- `contract/get` and MCP `get_contract` now expose bounded verification history,
  making request, consumption, and verifier-start state directly model-readable.
- `lhgp doctor` now checks launch executables for enabled registry entries and
  reports actionable missing-CLI diagnostics before dispatch.
- MCP now exposes read-only `lhgp_doctor` / `longtask_doctor` aliases so models
  can run the same preflight diagnostics before selecting an executor.
- L1 RTC wakeup targets now use the earliest available decision, wakeup, or
  deadline-safety point; a later safety margin can no longer postpone an
  earlier `next_decision_at`, and past decision points are clamped to an
  immediate wakeup.
- The Windows L1 adapter now arms one-shot Task Scheduler entries that call
  authenticated `daemon/wake`; fired signals are audited and consumed before
  the next decision tick, while non-Windows platforms remain explicit
  `wakeup/degraded` fallbacks. Its date serialization follows the actual
  Windows `schtasks.exe` `yyyy/mm/dd` parser, verified by a real create/query/
  delete smoke.
- L1 RTC wakeup targets now use the earliest available decision, wakeup, or
  deadline-safety point; a later safety margin can no longer postpone an
  earlier `next_decision_at`.

### Developer Preview release (2026-09-01 cut)

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
