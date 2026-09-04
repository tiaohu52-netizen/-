# Long-Horizon Goal Protocol (LHGP)

> [中文说明](./README.zh-CN.md)

Your goal should not die with the chat.

LHGP lets you place a goal under an independent, local contract: define the
outcome, acceptance checks, deadline, budget, and which agent CLIs and models
may work on it. Sessions may end and agents may change; the commitment,
evidence, and handoff state remain.

> **Session owns an attempt. LHGP owns the commitment.**

> **Read this first, plainly.** This is a **Developer Preview**. The full
> specification ([`docs/LHGP-SPEC.md`](docs/LHGP-SPEC.md)) is ahead of the
> implementation. The implementation roadmap is
> [`docs/LHGP-ROADMAP.md`](docs/LHGP-ROADMAP.md); each capability claim in
> this README is backed by a row in [`quality/claims.json`](quality/claims.json).
> Three things the protocol does **not** do: it does not work when your
> machine is off; the deadline is not a result guarantee; plugins and the
> protocol are not the same layer.

---

## Why you'd want it

You know the kind of task that has these problems:

- *"Take a look at this on Friday."* — you forget, or you remember at 11pm and don't want to deal with it.
- *"Try this in a different model to check."* — but you'll only remember to do it three weeks from now.
- *"This needs to run while I'm not here."* — and you want a record of exactly what got done.
- *"Don't start this until 2am."* — but you don't want to stay up to launch it.

Plain chat memory doesn't survive a session. A cron job doesn't know what
"done" looks like. A workflow tool doesn't carry the goal across agents.
LHGP is a **persistent commitment ledger with a local scheduler and an
executor pool**, living in a SQLite file on your laptop. The session that
drafted the goal is not the thing that finishes it.

If that sounds like what you want, read on. If you just want a chatbot that
remembers things, this isn't it.

## Goal / Contract / Attempt — three layers

| Layer | Lives in | Owned by | Lifetime |
|---|---|---|---|
| **Goal** | Your head, then a contract | You | Until you cancel or it terminates |
| **Contract** | `state.db` + `~/.lhgp/contracts/<id>/` | LHGP, not the session | Until terminal commitment state |
| **Attempt** | One running agent CLI process | A session | Until the agent exits or the lease expires |

The session that holds an attempt is replaceable. The contract is not. When
the lease expires, a different authorized agent can pick up where the last
one left off — without losing accepted evidence.

## What it does, in one paragraph

You write a **contract**: a goal, what "done" looks like, constraints,
deadline, and a budget for how many tries it's allowed. You approve it.
Then the local daemon (`lhgpd`) takes over:

1. **Watches the clock.** It knows when the contract is due and when you
   said it should wake up.
2. **Picks a runner.** When the time comes, it looks at the user-approved
   list of installed agent tools (Codex, Claude Code, agent-cli, anything that
   has a CLI) and picks one that can do the job.
3. **Runs it.** Spawns the runner with your task text and the work directory.
4. **Watches over it.** Keeps the lease alive while the runner is working;
   if it dies, takes the lease back and tries someone else.
5. **Checks the work.** After the runner reports success, it dispatches a
   **second, different** runner to verify the work against your acceptance
   criteria. If the verifier disagrees, it goes back and tries again.
6. **Tells you.** The contract reaches a terminal commitment state. The
   whole story — every event, every heartbeat, every check — is in
   `state.db` and in the file projections you can grep.

You can stop paying attention. Or watch along — it's a normal Unix daemon.

> The daemon only wakes up while your machine is on. If the lid is closed,
> the L0 power guard and L1 RTC alarm stand in for it (see §6.4 of the
> spec); L2 (cloud relay) and L3 (always-on relay) are designed but not
> deployed. The deadline is a scheduling hint, not a service-level
> guarantee.

## How this differs from things that look like it

| Tool | What it actually is | Why it isn't LHGP |
|---|---|---|
| Goal-mode chat | The same session keeps going until done | No contract; dies with the session |
| `cron` / launchd | Fires a script on a clock | No "done" check, no handoff |
| n8n / Temporal / LangGraph | Durable workflow engines | You're the operator; the agents aren't interchangeable and authorized under a contract |
| Vector memory DB | Stores chat history | No scheduler, no commitment, no acceptance |

LHGP is **not** a workflow engine. The agent can be replaced mid-run
under a still-valid lease; the goal cannot be replaced by the agent.

## What is actually implemented today

This is honest inventory. Each row is backed by an entry in
[`quality/claims.json`](quality/claims.json) — open it to see the evidence
path and the pinned commit for every claim.

| Capability | Status | Claim |
|---|---|---|
| Skeleton passes all seven quality gates | Verified | `skeleton-gates-green` |
| Urgency tiering per DESIGN §6.2 | Verified | `urgency-tier-thresholds` |
| Lease generation/holder fencing | Verified | `lease-fencing-logic` |
| Wire-protocol error code registry complete | Verified | `error-code-registry-complete` |
| Transactional SQLite store + WAL crash recovery | Verified | `persistence-transactional-writes` |
| Default-deny refusal paths (no silent degradation) | Verified | `refusal-never-degrades` |
| Escalation ladder (tier 0–5 with arbitration) | Verified | `escalation-ladder-decision` |
| JSON-RPC contract lifecycle + cursor pagination | Verified | `rpc-contract-lifecycle` |
| Executor registry, capability matching, cost-priority dispatch | Verified | `executor-registry-matching` |
| File projections + handover.md schema | Verified | `file-projections-and-handover` |
| CLI + daemon control plane + dry-run | Verified | `cli-and-daemon-control-plane` |
| Strict-deadline layered wakeup (L0/L1 local adapter done; L2/L3 designed, not deployed) | Accepted debt (review by 2026-12-01) | `strict-deadline-wakeup-design` |
| MCP server + model-facing skill (core tools, LHGP aliases, contract/Goal reads, decision/attempt/verification history, doctor preflight, audit, and control extensions) | Verified | `mcp-server-and-skill` |
| Ephemeral context + cross-checking verifier | Verified | `ephemeral-context-and-verifier` |
| User-triggered verification without re-dispatching an executor | Verified | `ephemeral-context-and-verifier` |
| Executor-side RPC (status / renew / write-back) | Verified | `executor-session-rpc` |
| Authenticated local Unix-socket RPC (daemon + `rpc-call` client) | Verified | `local-rpc-transport` |
| Durable notification outbox (idempotency, retry, quiet hours) | Verified | `notification-outbox` |
| State and risk notifications (`need_user` / `satisfied` / `missed` / `risk_red`) | Verified | `notification-routing` |
| Daemon lifecycle + attempt runner (real subprocess) | Verified | `daemon-lifecycle-and-attempt-runner` |

What this list **does not** claim:

- Strict completion guarantees at a wall-clock deadline. The daemon is a
  best-effort local scheduler and reports risk rather than promising delivery.
- Full seven-kind automatic acceptance for every check. Deterministic checks
  are supported; model/human review and composite policies still require an
  explicit evidence-producing verifier.
- Multi-host or cloud relay execution. The current trust boundary is one local
  machine and one user; L2/L3 wakeup remains accepted design debt.
- A network or multi-host JSON-RPC transport. The current socket is local-only
  and authenticated with the token in `daemon.token`.
- External notification channels (email, webhook, or mobile push). The current
  `local` channel is a durable callback/log sink intended for adapters to extend.

These gaps are tracked explicitly in [`docs/LHGP-ROADMAP.md`](docs/LHGP-ROADMAP.md).

## 30 seconds from clone to first task

You need Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) (`pip install uv`).

```bash
# replace <OWNER> with the GitHub account that hosts this repository
git clone https://github.com/<OWNER>/longtask-protocol
cd longtask-protocol
uv sync --extra dev
uv run python scripts/quality_gate.py   # ~10s; the same gate CI runs
uv run lhgp doctor
```

You should see something like:

```
[gate] ALL PASS (7 gates)
=== LHGP doctor (v0.1.0a0, protocol v1) ===
[PASS] python_runtime: Python 3.13.x
[PASS] storage_directory: ~/.lhgp accessible
[PASS] database_integrity: state.db healthy
[PASS] executor_registry: registry accessible (0 enabled / 0 registered)
```

Done. You now have a working local LHGP install. The primary commands are
`lhgp`, `lhgpd` and `lhgp-mcp`; the legacy `longtask`, `longtaskd` and
`longtask-mcp` aliases remain available during the migration window.

Once the daemon is running, another agent or process can use the same control
plane without opening the SQLite database directly:

```bash
lhgp start
lhgp rpc-call attempt/status \
  --params '{"contract_id":"<id>","attempt_id":"<attempt>"}'
```

The client reads the local endpoint token automatically. Socket access is
single-machine by design; cross-host relay is a separate trust-boundary feature.

### Your first contract, end to end

You need a runner. For now, register anything with a CLI that exits 0
when it's done:

```bash
# tell LHGP about a runner (here: just `echo`, as a sanity check)
cat > /tmp/my-runners.json <<'EOF'
{
  "agents": [{
    "id": "echo-runner",
    "kind": "subprocess",
    "launch": { "argv": ["/bin/sh", "-c", "echo done > $WORKSPACE/result.txt"] },
    "capabilities": {},
    "limits": {},
    "cost_hint": "low",
    "enabled": true
  }]
}
EOF
# point at it (path can be anything; it's config data, not a CLI flag)
cp /tmp/my-runners.json ~/.lhgp/registry.json
```

Write a contract:

```bash
lhgp prepare \
  --contract-id lt-20260903-hello \
  --title "First contract" \
  --objective "Write hello.txt with 'hi from LHGP' in my workspace." \
  --deadline 2026-12-31T00:00:00+00:00
```

Approve it:

```bash
lhgp approve lt-20260903-hello
```

Start the daemon in another terminal:

```bash
lhgp start --interval 30
# leave it running; it'll pick up the contract, run echo, verify, complete.
```

Check:

```bash
lhgp get lt-20260903-hello
lhgp status    # daemon / kill-switch
# inspect durable notification delivery (payload is hidden by default)
lhgp notifications --status pending
# narrow the audit to one long-horizon goal when several run in parallel
lhgp notifications --goal-id lt-20260903-hello
# add --include-payload only when the audit needs notification details
cat ~/.lhgp/contracts/lt-20260903-hello/contract.yaml
```

The whole story of the run — `contract/prepared` → `contract/approved` →
`attempt/started` → `attempt/succeeded` → `contract/completed` — is in
`state.db` and mirrored into `~/.lhgp/contracts/lt-20260903-hello/`.

### If your agent is an MCP-compatible LLM

The package installs `lhgp-mcp` (with the legacy `longtask-mcp` alias), a thin
[MCP](https://modelcontextprotocol.io) server that exposes the core task-flow tools plus LHGP-named aliases, audit,
and control extensions (not a 1:1 tunnel onto the 24 RPC methods — see the
spec on §11.1). Point your harness at it
(usually one line in your MCP config) and the model can discover and use
the protocol directly — see [`skills/longtask-contract/SKILL.md`](skills/longtask-contract/SKILL.md)
for the model-side onboarding.

## What it explicitly does not do (yet)

- **Multi-host.** Single laptop, single user. If you want it on a server farm, you're too early.
- **Multi-tenant.** No auth, no accounts. Local file trust boundary only.
- **Network wakeup (cloud relay).** The protocol has a four-layer wakeup spec; L0 (local power) and L1 (RTC) are in, L2 (cloud) and L3 (relay) are designed but not implemented. The daemon falls back gracefully when they're unavailable. See the `strict-deadline-wakeup-design` claim in the registry.
- **A web UI.** LHGP is files + CLI + RPC. The file projections (`contract.yaml`, `lease.json`, `log.jsonl`) are the human interface — version-controllable, greppable, scriptable.
- **Result guarantee at the deadline.** The deadline drives *when* the daemon decides to escalate; it does not guarantee the work is finished on time.
- **Survives the laptop being off forever.** L0/L1 cover sleep and lid-closed states only. Truly offline, always-on wakeup requires L2/L3, which are tracked as accepted debt.

## Where things live in this repo

```
docs/LHGP-SPEC.md         the protocol specification (single source of truth for semantics)
docs/LHGP-ROADMAP.md      the implementation roadmap (single source of truth for ordering)
README.md                 you are here
README.zh-CN.md           中文说明
LICENSE                   Apache-2.0
SECURITY.md               threat model + how to report
CONTRIBUTING.md           how to participate
CODE_OF_CONDUCT.md        community standards
CHANGELOG.md              what changed in each version
schemas/                  machine-readable JSON Schemas for the wire protocol

src/longtask/             the reference implementation, Python 3.11+, zero runtime deps
  contracts/              contract schema + validation
  persistence/            SQLite store + file projections + §4.1 ephemeral context
  scheduler/              ticker + wakeup
  promoter/               urgency, escalation ladder, lease + fencing
  adapters/               how to wrap a CLI runner (Codex / Claude / agent-cli / ...)
  rpc/                    JSON-RPC control plane
  cli/                    the `lhgp` command + `lhgpd` daemon (legacy names remain)
  mcp_server.py           the `lhgp-mcp` model-facing entry (legacy name remains)
src/lhgp/                 canonical namespace facades (migration-safe, same implementation)

skills/longtask-contract/ onboards an AI to use the protocol
quality/                  governance: claims registry + 7 quality gates
  claims.json             capability claims with evidence paths and pinned_sha
  claim-schema.json       the schema the registry is validated against
docs/decisions/           ADRs (architecture decision records)
examples/                 real-run archives (do not edit — they're audit evidence)
  agent-cli-model-provider-run/        first end-to-end with DeepSeek Harness + model-provider-M2.7
  agent-cli-model-provider-run-v2/     same task with ephemeral context + verifier enabled
  mcp-discovery/          8-step contract lifecycle via the MCP server
tests/                    unit / integration / conformance
scripts/                  the 7-gate quality runner (local == CI)
.github/workflows/        multi-OS CI on every push
```

## Help, it's not working

- **First stop**: `uv run lhgp doctor`. It runs local sanity checks, including enabled CLI launchability, and tells you which one failed.
- **Stuck contract**: `uv run lhgp get <id>`. Look at `state`, `blocked_reason`, and the events.
- **Kill switch**: `uv run lhgp kill-switch --activate` halts all dispatching immediately. Re-arming is `--deactivate`.
- **Daemon wedged**: `uv run lhgp stop`, then `start` again. State is preserved.
- **Found a bug?** File it via the [bug report template](../../issues/new?template=bug_report.md). Include `uv run python scripts/quality_gate.py` output if you ran it.
- **Asking a design question**: check [`docs/LHGP-SPEC.md`](docs/LHGP-SPEC.md) first (it's 1100+ lines but searchable). If still unclear, use the [documentation template](../../issues/new?template=documentation.md).

## Maturity

This is the **0.1.0a0 "Developer Preview"** release. The specification is
ahead of the implementation; the implementation roadmap
([`docs/LHGP-ROADMAP.md`](docs/LHGP-ROADMAP.md)) tracks the gap. The
real-world validation so far is a few test runs of "write a palindrome
checker" with an AI model as the runner, and they went end-to-end (see
`examples/`). The local contract, evidence, model-attestation, handover and
daemon paths are implemented; the remaining rough edges are strict deadline
guarantees, multi-host wakeup, and a network control-plane transport.

If you're an early user, expect: rough edges, missing docs in places,
occasional gate noise. The 7 quality gates and the claims registry exist
exactly to keep those honest.

## Naming migration window (P6 dual-track is live)

The protocol is **LHGP** (`Long-Horizon Goal Protocol`). As of P6 the new
entry points are shipped **alongside** the old ones (dual-track, SPEC §19.3):

- `lhgp` / `lhgpd` / `lhgp-mcp` — new names, same behavior as
  `longtask` / `longtaskd` / `longtask-mcp`;
- data directory: fresh installs use **`~/.lhgp`**; existing installs keep
  reading `~/.longtask` until migrated;
- `lhgp migrate` — moves `~/.longtask` → `~/.lhgp` with the safety defaults
  on: prints the plan only (dry-run) unless you pass `--execute`, makes a
  full backup under `~/.lhgp-migration-backups/`, and copies (never moves)
  so rollback = delete the new directory. Old names remain working aliases
  for at least one minor release. See
  [`docs/LHGP-ROADMAP.md`](docs/LHGP-ROADMAP.md) §P6 for the cut-over plan.
