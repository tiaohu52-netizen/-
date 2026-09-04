---
name: long-horizon-goals
description: 将需要跨会话、跨 Agent 推进的用户目标转成 LHGP 合同，明确授权、验收、Deadline、预算和交接边界。
---

# long-horizon-goals

> LHGP 协议面向 AI 的 skill 教学。配合 `lhgp-mcp` 使用；`longtask-mcp`
> 仅作为迁移窗口内的兼容别名。

## 何时触发

当用户**委托一个跨会话的任务**给你，且该任务：
- 超过单次对话上下文窗口
- 可能需要"歇一歇再继续"（即分散在多个时间点执行）
- 需要可被审计（用户事后要回看每一步）

→ 触发本 skill：把任务转成 LHGP 合同 + 提交给 `lhgp-mcp`，而非自己直接做。

## 核心公理

> **会话只持有一次尝试，LHGP 持有长期承诺。**

你的会话可能随时结束（上下文耗尽、用户关闭、模型服务重启）。
你起草的合同**不会**因为你的会话结束而消失——daemon 在本机持有它，
按 Deadline 风险推进或换另一个会话继续。

## 七步走通 MCP 工具链

1. `health` — 确认 daemon 在线
2. `list_executors` — 查可用执行器池（Codex / Claude Code / DSH / 任何 CLI 适配器）
3. `prepare_contract` — 起草合同：objective / acceptance / deadline / budget / authority
4. `approve_contract` — 用户确认后批准（drafted → active）
5. `get_contract` — 看运行状态、当前 attempt、leasing，以及该合同隔离的
   `decision_history`（风险档、升级原因、预算余量和下一步依据）；可传
   `decision_limit` 控制决策历史条数，`attempt_limit` 控制 attempt 历史条数
6. （等待 daemon 派 attempt；轮询 get_contract 或订阅 events）
7. `list_contracts` — 复盘历史 + 审计事件链

调用 `get_contract` 后，优先读取返回的 `verification_history`：
`verification/requested` 表示用户已请求验收，`verification/consumed` 表示
daemon 已接受并处理请求，`verification/started` 表示 verifier 已经启动。
只有看到后两者之一后才进入等待或读取 `attempt_history`；不要因为请求已写入
就臆测 verifier 已经运行。

如果执行预算已经耗尽、但工作区可能已经满足验收，不要重新起草或继续派
executor；调用 `lhgp_request_verification`（兼容名
`longtask_request_verification`）请求只验收当前交付物。它写入
`verification/requested`，由 daemon 下一次 tick 幂等派生独立 verifier；终态、
已有 verifier 运行中或验证预算耗尽时必须接受协议拒接并按提示升级。

## 运行中审计与控制（扩展工具）

- `lhgp_notifications` 是只读通知 outbox 视图；优先按 `goal_id` 或
  `status` 缩小范围，默认不请求 `include_payload`，避免把上下文内容带回对话。
- `lhgp_attempt_status` 用于查看某次 attempt 的事件、租约和当前状态。
- `lhgp_interrupt_attempt` 仅在用户明确要求停止时调用；它只写入中断请求，
  由 daemon 在安全仲裁点兑现。
- `lhgp_write_back` 只接受执行者持有的 generation，并必须携带真实进度或
  evidence；不要用它伪造完成状态。

没有 MCP 时，可用等价的只读 CLI 审计：
`lhgp notifications --goal-id <id>`（payload 默认隐藏）。

## 起草合同时必须写清

- **objective** — 写验收，不是方法（"输出 result.txt 含 'hi'" 而非 "用 python 写"）
- **acceptance.checks** — 逐条可独立核对（不要 "看起来对就行"）
- **workload_initial_hours** — 如实填（决定紧迫度档位）
- **authority.executors** — 你知道哪些 CLI 可用；未列即拒（default-deny）
- **deadline** — 给时区，ISO-8601

## 不要做

- 不要把模型输出塞进 argv 或环境变量（注入防线 §14）
- 不要编造检查通过——failed 就是 failed
- 不要把"看起来对"算 pass——必须 evidence
- 不要试图执行合同——交给 daemon 派 attempt
