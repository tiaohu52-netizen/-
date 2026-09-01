# 端到端真实执行记录：协议唤起 DSH headless（MiniMax-M2.7-highspeed）完成代码任务

日期：2026-09-01。本目录是一次真实运行的归档（配置数据与交付物，
**非协议代码**——协议本身零改动，任何 headless CLI 执行器走同一通用路径接入）。

## 运行拓扑

- 执行器：`dsh-headless`（注册表配置，见 `registry.example.json`）——
  `node <dsh lib/bin.js> --profile headless`，任务文本作为单个 argv 尾元素
  （协议的 task_prompt 通道，DESIGN §12.1）。
- 服务模型：DSH 后端 `minimax-cn` provider 的 **MiniMax-M2.7-highspeed**
  （模型与 provider 是 DSH 侧配置 `agent-default-model`，不在本协议任何代码里）。
- 合同：`lt-20260901-m2p7c`（回文检测工具模块，workspace 隔离，6 条验收 check）。

## 执行时间线（三轮 attempt，全部走协议记账）

| 轮次 | attempt | 结果 | 说明 |
|---|---|---|---|
| 1 | att-…-2p7b (首轮) | succeeded | MiniMax 交付 palindrome.py + tests.py；总控独立核验 6 条 check，第 6 条失败：执行者测试自带错误断言（`"No, it is averted, I: Verizon?"` 非回文却断言 True） |
| 2 | att-…-2p7b (第二轮) | failed | 再派修正，但 objective 未携带验收失败上下文——执行者见文件已存在即报完成，未改动。**真实暴露协议缺口：再派 attempt 的任务文本不包含交接内容（DESIGN §4.1 临时上下文未实现的直接后果）** |
| 3 | att-…-2p7c | succeeded | 失败详情写入工作区 FIX-NOTES.md + 任务文本指读它；MiniMax 按 assertFalse 修正断言 |

最终验收：**6/6 check 通过**（文件存在 ×2、签名、Panama→True、Hello→False、
unittest 5 用例全绿，全部由总控独立核验，不采信执行者自述）。合同已转
`complete`（事件带 verifier 证据）。

## 复现要点

1. 注册表：把 `registry.example.json` 放进数据目录（argv 指向你的 dsh 入口；
   env_allowlist 含 DSH_HOME 与所选 provider 的 key 环境变量名）。
2. 立合同（workspace 隔离 + 可核对 checks）→ approve → daemon tick 或
   AttemptRunner 拉起。
3. 验收：总控逐条独立核验 acceptance.checks，通过才 complete（§5.2）。

## 本次运行暴露的协议缺口（候选改进，均未动手）

1. **§4.1 临时上下文**：再派 attempt 时交接/验收失败上下文进不了任务文本
   ——第二轮因此浪费一次预算。实现 active.md 快照编译器后应把
   handover 摘要并入 task_prompt 或 context_snapshot_path。
2. **执行者侧 RPC**（lease/renew、attempt/status、write_back 暴露）：
   执行中 attempt 的收尾依赖 daemon 进程持有 Popen 句柄；跨进程轮询
   丢句柄后只能租约超时兜底或人工补录（本次第一轮走了人工补录）。
3. **verifier 角色**（§5.2）：本次由总控（人）充当；协议侧 verifier
   attempt 的自动派出仍未实现。
