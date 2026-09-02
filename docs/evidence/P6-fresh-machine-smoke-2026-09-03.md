# P6 fresh-machine smoke evidence

Date: 2026-09-03  
Artifact: `longtask_protocol-0.1.0a0-py3-none-any.whl`  
Environment: new CPython 3.13 virtual environment created with `uv venv`

## Checks performed

1. Installed the wheel with `uv pip install --no-deps` in an isolated environment.
2. Ran `lhgp --version` → `lhgp 0.1.0a0 (protocol v1)`.
3. Ran `longtask --version` → `longtask 0.1.0a0 (protocol v1)` (compatibility alias).
4. Sent a `tools/list` JSON-RPC request to `lhgp-mcp`; the response contained the
   LHGP aliases, notification audit, interrupt, and write-back tools.
5. In a clean data directory, ran `prepare`, `get`, `approve`, `status`, and
   `notifications --goal-id` for `lt-20300101-qs-smoke`. The contract moved from
   `drafted` to `active` and the read-only commands returned successfully.
6. Verified both wheel and sdist with `scripts/check_artifacts.py`; all companion
   plugin and Skill resources were present.
7. Ran `examples/dsh-dogfood-v4/dogfood_v4.py probe` twice on Windows. Both runs
   selected only `dsh-headless` for the executor role and only `kimi-code` for the
   verifier role, rejecting the enabled `codex-cli` interference entry.
8. Ran the complete `tests/integration/test_reconcile.py` suite: 20 tests passed,
   covering real subprocess handles, restart/reattach, orphan grace, fencing,
   redispatch, and post-reap settlement.
9. Ran `tests/integration/test_p5_repair_loop.py`: 8 tests passed, covering
   verifier failure, structured repair, re-verification, and budget exhaustion.
10. In a clean temporary data directory, started the daemon with a one-second
    interval, confirmed `status` reported the live PID and token, stopped it, and
    confirmed a second `status` reported `running: false`.

## Scope and remaining work

This proves package installation and the basic local quickstart path. It does not
prove daemon execution of a non-trivial external runner, cross-agent relay, or the
three Alpha dogfood goals; those remain release gates in `docs/LHGP-ROADMAP.md`.
