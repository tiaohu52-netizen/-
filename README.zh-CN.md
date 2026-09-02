# Long-Horizon Goal Protocol (LHGP) — 远期目标协议

> [English README](./README.md)

你的目标不该随着聊天一起死掉。

LHGP 让你把一个目标放在一份独立、本地的合同里：定义结果、验收标准、Deadline、预算，以及允许哪些 Agent CLI 与模型去执行它。会话可以结束，Agent 可以更换；承诺、证据、交接状态始终保留。

> **会话只持有一次尝试，LHGP 持有长期承诺。**

> **先把丑话说在前面。** 这是一份 **Developer Preview（开发者预览版）**。完整规范（[`docs/LHGP-SPEC.md`](docs/LHGP-SPEC.md)）领先于实现；实现路径见 [`docs/LHGP-ROADMAP.md`](docs/LHGP-ROADMAP.md)。本 README 列出的每项能力，都能在 [`quality/claims.json`](quality/claims.json) 里查到证据路径与对应 commit。协议明确**不做**的事有三件：机器离线时不工作；Deadline 不是结果担保；插件与协议不是同一层。

---

## 你为什么需要它

下面这些任务你大概都遇到过：

- *"周五前帮我看一眼这个。"* —— 结果你忘了，或者拖到晚上 11 点再也不想处理。
- *"用另一个模型跑一下对比看看。"* —— 三周之后你才想起来要做这件事。
- *"这个任务要在我不在的时候自己跑完。"* —— 你想要一份精确的完成记录。
- *"凌晨两点再启动，不要现在跑。"* —— 你不想为这一件事熬到深夜。

普通聊天的记忆撑不过一次会话；`cron` 不知道什么叫"做完了"；工作流工具不会在多个 Agent 之间交接目标。LHGP 是一个 **持久承诺账本 + 本地调度器 + 执行者池**，就放在你笔记本电脑的 SQLite 文件里。最初起草目标的会话，不是最终完成目标的那个会话。

如果这正是你要的，继续读。如果你要的只是"能记住事的聊天机器人"，那它不是。

## Goal / Contract / Attempt —— 三层结构

| 层 | 存放 | 由谁拥有 | 生命周期 |
|---|---|---|---|
| **目标** | 你的脑子里，然后落到合同里 | 你 | 直到你取消或终结 |
| **合同** | `state.db` 与 `~/.lhgp/contracts/<id>/` | LHGP，不是会话 | 直到承诺状态终结 |
| **尝试** | 一个跑着的 Agent CLI 进程 | 一个会话 | 直到该 Agent 退出或租约过期 |

持有"尝试"的会话是可替换的；合同不可替换。租约到期后，另一个被授权的 Agent 可以从上次验证通过的进度接着干，**已验收的证据不会丢失**。

## 一段话讲清它做了什么

你写一份 **合同**：目标、"完成"的定义、约束、Deadline、可重试的预算。你批准它，本地守护进程（`lhgpd`）接手：

1. **盯着时钟。** 它知道合同什么时候到期，也知道你说该什么时候醒来。
2. **挑一个 Runner。** 时间到了，它从你批准过的 Agent 列表里（Codex / Claude Code / DSH / 任何有 CLI 的工具）挑一个合适的。
3. **拉起它。** 把任务文本和工作目录交给 Runner。
4. **看守它。** Runner 干活时持续续约；如果它死了，把租约收回换人。
5. **交叉核对。** Runner 报成功后，派一个**不同的** Runner 按你的验收标准核验。核对不过，退回去重试。
6. **通知你。** 合同进入终止承诺状态。整个故事（每一个事件、每一次心跳、每一项核验）都在 `state.db` 和可 grep 的文件投影里。

你可以不再盯着；也可以继续看 —— 它就是一个普通的 Unix 守护进程。

> 守护进程只在你的机器开机时才醒。合上盖子时由 L0 电源守卫和 L1 RTC 闹钟顶上去（见规范 §6.4）；L2（云端中继）与 L3（常在线中继）已完成设计但尚未部署。Deadline 是调度提示，不是 SLA。

## 它和看着像的东西有什么不一样

| 工具 | 它实际是什么 | 为什么它不是 LHGP |
|---|---|---|
| 聊天的 Goal mode | 同一个会话持续跑到结束 | 没有合同；会话死了它就死 |
| `cron` / launchd | 按时钟触发一段脚本 | 没有"完成"判定，没有交接 |
| n8n / Temporal / LangGraph | 持久化工作流引擎 | 你是操作员；Agent 不可在合同授权下互换 |
| 向量记忆数据库 | 存聊天历史 | 没有调度、没有承诺、没有验收 |

LHGP **不是**工作流引擎。Agent 可以在租约仍然有效时被换掉；目标不能被 Agent 替换。

## 今天到底落地了哪些能力

这是一份诚实的清单。每一行都对应 [`quality/claims.json`](quality/claims.json) 里的一条声明 —— 打开它就能看到证据路径与 pinned_sha。

| 能力 | 状态 | 声明 |
|---|---|---|
| 骨架代码过全部七道质量门 | 已验证 | `skeleton-gates-green` |
| 紧迫度分档（DESIGN §6.2） | 已验证 | `urgency-tier-thresholds` |
| 租约 generation + holder fencing | 已验证 | `lease-fencing-logic` |
| 线协议错误码注册表完整 | 已验证 | `error-code-registry-complete` |
| SQLite 事务写 + WAL 崩溃恢复 | 已验证 | `persistence-transactional-writes` |
| 默认拒接（无静默降级） | 已验证 | `refusal-never-degrades` |
| 升级阶梯（档 0–5 + 仲裁） | 已验证 | `escalation-ladder-decision` |
| JSON-RPC 合同生命周期 + cursor 分页 | 已验证 | `rpc-contract-lifecycle` |
| 执行者注册表 + 能力匹配 + 成本优先级分发 | 已验证 | `executor-registry-matching` |
| 文件投影 + handover.md 模式 | 已验证 | `file-projections-and-handover` |
| CLI + 守护进程控制面 + --dry-run | 已验证 | `cli-and-daemon-control-plane` |
| 严格 Deadline 分层唤醒（L0/L1 已落地；L2/L3 仅设计） | 已接受的债（2026-12-01 复审） | `strict-deadline-wakeup-design` |
| MCP server + 模型侧 skill（核心工具、LHGP 别名及审计/控制扩展，不是 1:1 RPC 隧道） | 已验证 | `mcp-server-and-skill` |
| 临时上下文 + 交叉核对 verifier | 已验证 | `ephemeral-context-and-verifier` |
| 执行者侧 RPC（status / renew / write-back） | 已验证 | `executor-session-rpc` |
| 守护进程生命周期 + AttemptRunner（真实子进程） | 已验证 | `daemon-lifecycle-and-attempt-runner` |

这份清单**没有**声称：

- 四轴承诺状态（承诺生命周期 / Deadline / 验收 / 尝试）—— 规范要求四轴，目前实现只有两轴。
- 跨执行者 × 模型 × 角色的 default-deny 授权（合同下一份统一 allowlist）。verifier 会被派，但三维 allowlist 还未强制。
- 外部运行句柄（`external_run_id`、`session_locator`、`recovery_strategy`、`capability_snapshot`），守护进程无法接手它不拥有的 harness 派出的工作。
- 六分量 forecast（queue / startup / remaining work / verification / retry reserve / safety margin）驱动事件式唤醒。
- 类型化的验收 checks 与 `evidence` 实体，以及结构化的 repair brief。
- 完整的跨主机插件分发与全新机器 dogfood（P6 双轨入口、MCP 工具迁移和
  `~/.lhgp` 默认目录已上线；剩余工作见 ROADMAP）。

这些差距在 [`docs/LHGP-ROADMAP.md`](docs/LHGP-ROADMAP.md) 中被显式跟踪。

## 30 秒从 clone 到第一个任务

你需要 Python 3.11+ 和 [`uv`](https://docs.astral.sh/uv/)（`pip install uv`）。

```bash
git clone https://github.com/your-org/longtask-protocol
cd longtask-protocol
uv sync --extra dev
uv run python scripts/quality_gate.py   # 约 10 秒，与 CI 跑的是同一道门
uv run python -m longtask.cli.main doctor
```

你应该看到：

```
[gate] ALL PASS (7 gates)
=== longtask doctor (v0.1.0a0, protocol v1) ===
[PASS] python_runtime: Python 3.13.x
[PASS] storage_directory: ~/.lhgp accessible
[PASS] database_integrity: state.db healthy
[PASS] executor_registry: registry accessible (0 enabled / 0 registered)
```

这样你就装好了一份本地可用的 LHGP。新入口是 `lhgp` / `lhgpd` / `lhgp-mcp`；
旧的 `longtask` / `longtaskd` / `longtask-mcp` 仍作为兼容别名保留，数据目录
默认使用 `~/.lhgp`，旧安装会继续读取 `~/.longtask`。

### 端到端走一遍你的第一份合同

你需要一个 Runner。眼下，任何"完成时退出码 0"的 CLI 都可以先顶上：

```bash
# 告诉 LHGP 有一个 Runner（这里就用 echo 当冒烟测试）
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
# 指给它（路径随意，这是配置数据，不是 CLI flag）
cp /tmp/my-runners.json ~/.longtask/registry.json
```

写合同：

```bash
uv run python -m longtask.cli.main prepare \
  --contract-id lt-hello \
  --title "第一份合同" \
  --objective "在工作区写下 hello.txt，内容是 'hi from LHGP'。" \
  --deadline 2026-12-31T00:00:00+00:00
```

批准：

```bash
uv run python -m longtask.cli.main approve lt-hello
```

在另一个终端启动守护进程：

```bash
uv run python -m longtask.cli.main start --interval 30
# 留着它跑；它会接合同、跑 echo、核验、完成。
```

查看：

```bash
uv run python -m longtask.cli.main get lt-hello
uv run python -m longtask.cli.main status    # 守护进程 / 紧急熔断
# 查看持久化通知投递状态（默认隐藏 payload）
uv run python -m longtask.cli.main notifications --status pending
# 多个目标并行时，可按目标 ID 缩小审计范围
uv run python -m longtask.cli.main notifications --goal-id lt-hello
# 只有审计确实需要时才加 --include-payload
cat ~/.longtask/contracts/lt-hello/contract.yaml
```

整段运行的故事 —— `contract/prepared` → `contract/approved` →
`attempt/started` → `attempt/succeeded` → `contract/completed` —— 都在
`state.db` 里，也镜像到 `~/.longtask/contracts/lt-hello/` 下。

### 如果你的 Agent 是兼容 MCP 的 LLM

包还装了 `longtask-mcp`，一个薄的 [MCP](https://modelcontextprotocol.io)
server。它给你的模型暴露核心任务流工具，以及 LHGP 命名别名、审计和控制扩展（不是 24 个 RPC 方法的 1:1 隧道 —— 见规范 §11.1）。在你的 MCP 配置里加一行把它指给 harness，模型就能直接发现并使用本协议 —— 模型侧的接入文档在 [`skills/longtask-contract/SKILL.md`](skills/longtask-contract/SKILL.md)。

## 明确不做（暂时）

- **多主机。** 单机单用户。要上服务器集群？来早了。
- **多租户。** 没有认证，没有账户。本地文件信任边界。
- **网络唤醒（云端中继）。** 协议定义了四层唤醒；L0（本地电源）与 L1（RTC 闹钟）已落地，L2（云端）与 L3（中继）仅设计未实现。守护进程在这些通道不可用时会优雅降级。详见声明 `strict-deadline-wakeup-design`。
- **Web UI。** LHGP 就是文件 + CLI + RPC。文件投影（`contract.yaml`、`lease.json`、`log.jsonl`）就是人机界面 —— 可以版本管理、grep、脚本化。
- **Deadline 即结果担保。** Deadline 只决定守护进程**何时**升级，不会保证工作一定按时完成。
- **永久离线也能工作。** L0/L1 只能覆盖睡眠与合盖状态。真正离线 + 常在线唤醒需要 L2/L3 —— 已在 accepted_debt 中跟踪。

## 仓库结构

```
docs/LHGP-SPEC.md         协议规范（语义的唯一真相源）
docs/LHGP-ROADMAP.md      实施路径（顺序的唯一真相源）
README.md                 你在这里（英文）
README.zh-CN.md           中文说明
LICENSE                   Apache-2.0
SECURITY.md               威胁模型 + 漏洞上报
CONTRIBUTING.md           参与方式
CODE_OF_CONDUCT.md        社区准则
CHANGELOG.md              每次版本改了什么
schemas/                  线协议的 JSON Schema

src/longtask/             参考实现，Python 3.11+，零运行时依赖
  contracts/              合同 schema + 校验
  persistence/            SQLite 存储 + 文件投影 + §4.1 临时上下文
  scheduler/              滴答 + 唤醒
  promoter/               紧迫度、升级阶梯、租约与 fencing
  adapters/               怎么包一个 CLI Runner（Codex / Claude / DSH / ...）
  rpc/                    JSON-RPC 控制面
  cli/                    `longtask` 命令 + `longtaskd` 守护进程
  mcp_server.py           `longtask-mcp` 模型侧入口

skills/longtask-contract/ 让 AI 学会使用本协议
quality/                  治理：声明注册表 + 7 道质量门
  claims.json             每条能力声明 + 证据路径 + pinned_sha
  claim-schema.json       注册表的校验 schema
docs/decisions/           架构决策记录（ADR）
examples/                 真实运行归档（不要改 —— 是审计证据）
  dsh-minimax-run/        第一次端到端（DeepSeek Harness + MiniMax-M2.7）
  dsh-minimax-run-v2/     同任务，开启临时上下文 + verifier
  mcp-discovery/          通过 MCP server 的 8 步合同生命周期
tests/                    单元 / 集成 / conformance
scripts/                  7 道质量门运行器（本地 == CI）
.github/workflows/        每次 push 多 OS CI
```

## 出问题了

- **先看这个**：`uv run python -m longtask.cli.main doctor`。它跑 4 项自检并告诉你哪一项挂了。
- **合同卡住了**：`uv run python -m longtask.cli.main get <id>`。看 `state`、`blocked_reason` 与事件。
- **紧急熔断**：`uv run python -m longtask.cli.main kill-switch --activate` 立即停止所有派工；`--deactivate` 恢复。
- **守护进程僵死**：`uv run python -m longtask.cli.main stop`，再 `start`。状态会被保留。
- **找到 bug？** 用 [bug 报告模板](../../issues/new?template=bug_report.md) 提交。如果跑了门，附上 `uv run python scripts/quality_gate.py` 的输出。
- **问设计问题？** 先翻 [`docs/LHGP-SPEC.md`](docs/LHGP-SPEC.md)（1100+ 行，可搜）。还有疑问，用 [文档模板](../../issues/new?template=documentation.md)。

## 成熟度

这是 **0.1.0 "Developer Preview"**。规范领先于实现；实现路径见 [`docs/LHGP-ROADMAP.md`](docs/LHGP-ROADMAP.md)。现实世界的验证还停留在"让 AI 写个回文判定器"的几轮端到端跑通（见 `examples/`）。骨架是稳的；粗糙的地方集中在：授权强制、外部运行句柄、Deadline 驱动的唤醒、类型化验收、公开发插件与分发 —— 这些都被 ROADMAP P2–P6 显式跟踪。

作为早期用户，请预期：边角粗糙、部分文档缺失、质量门偶有噪音。7 道质量门与声明注册表正是为了让这些不被打肿脸充胖子。

## 改名窗口（P6 双轨已上线）

协议正式名是 **LHGP**（`Long-Horizon Goal Protocol`）。P6 起按双轨策略（SPEC §19.3）新旧名并存：

- `lhgp` / `lhgpd` / `lhgp-mcp` —— 新入口，与 `longtask` / `longtaskd` / `longtask-mcp` 行为完全一致；
- 数据目录：全新安装直接用 **`~/.lhgp`**；旧安装未迁移前继续读 `~/.longtask`；
- `lhgp migrate` —— 把 `~/.longtask` 迁到 `~/.lhgp`，安全默认全开：只打印计划（dry-run），传 `--execute` 才真跑；真跑前完整备份到 `~/.lhgp-migration-backups/`；用拷贝而不是移动——回滚 = 删掉新目录。旧名至少在一个次版本内继续可用。见 [`docs/LHGP-ROADMAP.md`](docs/LHGP-ROADMAP.md) §P6 的切换计划。
