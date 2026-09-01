# 端到端真实执行 v2：上下文通道 + verifier 交叉核对（DESIGN §4.1/§5.2）

日期：2026-09-01。本目录是 v2 归档——相对 v1（`../dsh-minimax-run/`），这次走通了：

- **§4.1 临时上下文**：执行者 attempt 收到 `active.md` 快照（事件 `context/snapshot-built`），
  task_prompt 含交接附言（这次无交接，留作下次回归证明——单纯快照通道
  在事件流里可见）；
- **§5.2 verifier**：执行者 succeeded 后 `AttemptRunner._dispatch_verifier`
  自动派生 verifier attempt（独立候选 `exec-b`，与执行者 `exec-a` 不同源），
  task_prompt 含 acceptance.checks 与标准全文；
- **verifier 裁决**：`daemon._judge_verifier_outcomes` 钩子在 tick 末扫 verifier
  终态事件，succeeded → 合同 `complete`（事件 `contract/completed` 带 verifier
  证据，actor=verifier）。

## 完整事件链（核心节选）

```
contract/prepared
contract/approved
lease/acquired            # 执行者代次 1（exec-a）
attempt/started           # role=executor, executor_id=exec-a
context/snapshot-built    # §4.1 active.md 落工作区
lease/renewed x90         # heartbeat 代发
attempt/succeeded         # 执行者 succeeded（dsh:session-3f4ae3e0）
lease/released
lease/acquired            # verifier 代次 1（exec-b）
attempt/started           # role=verifier, executor_id=exec-b, verifier_for=exec-a
attempt/succeeded         # verifier 全 check pass
lease/released
contract/completed        # actor=verifier，带 checks 证据
contract/state -> complete
```

## 已知设计缺口（如实记录）

执行者 succeeded 那一步不是观察自动触发的，需要 daemon 簿记补录：

- **根因**：SubprocessAdapter 把 dsh 主进程视为 attempt 的 Popen 句柄。
  dsh 是 headless harness，其 `node bin.js --profile headless` 的生命周期
  与 dsh 内部分发的 worker 进程对齐——主进程在 worker turn/end 后才会
  退出。我们的 Popen.poll() 看到主进程退出，observe 应该报非 running，但
  Popen 句柄在 `_procs[attempt_id]` 里仍持有，且 `_procs` 是字典引用，
  poll 进程跨进程重启时句柄就丢了。
- **影响**：daemon 必须能识别“harness 子进程”而非“Popen 句柄”作为
  attempt 终态信号。两种可行路径：
  1. harness 给 attempt 返回的可信 `session_ref` 含真 pid（DESIGN §5.1
     不可变五元组），daemon `OspProcess` 探活；
  2. harness 在标准输出/错误流写一条结构化终止事件（如
     `{"event":"attempt/finished",...}`），daemon parse。
- **当前方案**：dev 环境下由总控补录 attempt/succeeded 后再让 runner
    派生 verifier；生产环境需要选一条上路径。

## 与 v1 的差异

| 维度 | v1 (`../dsh-minimax-run/`) | v2（本目录） |
|---|---|---|
| 上下文快照 | 未实现（暴露“再派 attempt 缺验收上下文”缺口） | `context/snapshot-built` 落 `active.md` |
| Verifier 候选 | 仅人工总控验收（§5.2 缺口） | 自动派生独立 verifier + 裁决钩子 |
| Re-dispatch 失败 | 用 `FIX-NOTES.md` 作 workaround | 交接附言融 task_prompt（下次再派自带上下文） |
| Lease 心跳 | 由 daemon poll_attempts 代发 | 同 v1，事件流里 `lease/renewed x90` 可见 |
| 完成裁决 | 人工转 `complete` | daemon tick 钩子自动完成 |

## 复现

```bash
# 注册表：本目录 registry.example.json（argv/env_allowlist 与 dsh 入口对齐）
# 立合同（workload_initial_hours=2.0 让 u>=1.0 触发 RESPAWN 档）
# daemon tick + AttemptRunner：
#   res = run_daemon_tick(root, conn, reg, now=now)
#   runner.start_attempt(now, **res["attempts_started"][0])
#   runner.poll_attempts(now)  # 循环到 dsh 退出 → succeeded → 派生 verifier
# 总控验收（也可放工作区的人工核对）；再让 verifier 报告 succeeded
#   _judge_verifier_outcomes(root, conn, now)
```

## 文件清单

- `registry.example.json`：dsh headless 入口与 env_allowlist（脱敏）；
- `palindrome.py`、`tests.py`：MiniMax-M2.7-highspeed 交付物。