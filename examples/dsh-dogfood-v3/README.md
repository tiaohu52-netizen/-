# DSH 后端 dogfood v3：跨守护进程断裂 + reconcile 四分支真实运行

日期：2026-09-02。本目录是 v3 归档——用 DSH（DeepSeek Harness）headless +
MiniMax-M2.7-highspeed 作为**真实执行器后端**，验证 LHGP P0-P6 全链路在
真实外部 harness 下的行为。相对 v2（`../dsh-minimax-run-v2/`），这次走通了
断裂①（守护进程重启）并暴露+修复了一个真实结算缺口。

## 测试形态

- 执行器：`node <dsh>/bin.js --profile headless "<合同 prompt>"`（子进程）；
- 模型：MiniMax-M2.7-highspeed（临时 DSH_HOME 覆盖 `agent-default-model`，
  不动用户真实 `~/.dsh/settings.yaml`）；
- 任务：写 palindrome.py + tests.py 并自测（可客观验收）；
- 断裂模拟：phase1 拉起 dsh 后**进程直接退出**（内存 Popen 全部消失）；
  phase2 以全新进程跑 `run_daemon_loop`。

## 真实走通的链路

1. **合同三方可见性**：重派的 dsh 收到的 task_prompt 是完整合同冻结区
   摘要（objective + acceptance.checks 逐条 + 验收标准 + deadline +
   hard_constraints 含 workspace 边界 + 合同号/修订号）——P6 链路在真实
   harness 上兑现。
2. **句柄持久化 + 断裂①四分支**：spawn 后 `handle/registered` 落库
   （pid + 启动时间 + reattach 策略）；守护进程死亡后 reconcile 判定
   「状态未知」→ `attempt/orphaned` + **租约代持 5 分钟阻止重复 spawn**
   → 宽限期满 `reconcile/fenced-redispatched`（fence 旧代次）→ 自动重派
   新 attempt（新 dsh 进程 + 句柄再落库）。
3. **预算硬边界**：dispatch 预算（4 次）耗尽后合同转 `blocked`
   （`next-decision/set` 归因 "dispatch budget exhausted: only user
   action can move this contract"）——不假装、不无限重试。
4. **交付物真实**：`palindrome.py`/`tests.py` 由 MiniMax-M2.7-highspeed
   在 workspace 真实产出，`python tests.py` 通过（见本目录归档）。

## 本次暴露并修复的缺口（post-reap 结算）

**现象**：dsh 主进程退出后，持有 Popen 的 runner 进程若死亡，新进程的
reconcile 无法结算该 attempt——收尸后 `process_start_time` 读不到 →
身份不可证 → 只能走 orphan grace 白等 5 分钟（verifier 更痛：无租约
代持语义，永远挂着 running）。

**修复**（`promoter/reconcile.py`）：reattach 拒绝后补一次 **pid 死活
探测**——`process_alive(pid) is False`（确认进程不存在）即走分支 2
立即结算 failed（`exit_code_known=False` 如实标注，绝不猜退出码）。
这不违反 §11.3「pid 不单独作为身份真相」：身份（是不是同一 run）不判，
死活（进程在不在）如实判。仅对 reattach 策略句柄生效——poll/legacy
句柄的 pid 是解析提示（可能是占位数字），不得拿去判终态。

**实测效果**：修复前死 attempt 要 5 分钟 orphan grace；修复后两个死
attempt（执行者 27952 + verifier 32680）在同一轮 reconcile 立即结算
（terminal_at 同一秒，事件 `attempt/failed` 带 `collect_note: pid
confirmed gone...`）。

## 如实记录的未走通项

- **verifier-passed → complete 甜路径**未在本窗口走通：dsh 单次任务耗时
  超过 13 分钟轮询窗口（M2.7 对完整合同 prompt 跑多轮 worker），verifier
  终态在 loop 结束后才发生 → 被 post-reap 判 failed（诚实：证据不可得
  ≠ 成功）。这是观察窗口与任务时长的时间尺度错配，不是协议语义 bug
  （v2 归档记录过同类窗口问题，根因仍是 harness 子进程生命周期）。
- 执行 attempt 的 succeeded 判定同样受此影响：3 个 executor attempt 中
  2 个 orphan（宽限后 fence）、1 个 post-reap failed——工作产物在
  workspace 真实存在但终态判 failed（fail-closed 语义正确）。

## 文件清单

- `events.jsonl`：293 个事件的完整审计流（含断裂与重派全程）；
- `palindrome.py` / `tests.py`：MiniMax-M2.7-highspeed 真实交付物；
- `registry.example.json`：dsh headless 双执行器注册表（执行者+verifier）。

## 复现说明

本目录是 v3 的**结果归档**，当时使用的临时驱动脚本未随仓库提交，因而
不能通过上面的历史命令直接重放；不要把它当作当前可执行 quickstart。
可运行的无密钥 default-deny 探针见
[`../dsh-dogfood-v4/dogfood_v4.py`](../dsh-dogfood-v4/dogfood_v4.py) 的
`probe` 阶段：

```bash
uv run python examples/dsh-dogfood-v4/dogfood_v4.py probe
```

v3 的断裂、重派和事件链以本目录的 `events.jsonl` 为审计证据；要重做真实
LLM 阶段，需要自行准备 DSH、临时 `DSH_HOME` 和 `MINIMAX_CN_API_KEY`，不在
默认质量门范围内。
