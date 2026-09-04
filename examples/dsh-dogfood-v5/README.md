# dogfood v5：真实断裂考验（stage-1 归档）

日期：2026-09-04。本目录是 v5 的 stage-1 归档——相对 v4（甜路径），
v5 把**真实断裂**注入一个真实多阶段 Goal，验证 ROADMAP §1.1 Alpha
判据 1 的第一断裂形态。

## 目标（真实工作，非玩具）

Goal `lt-dogfood-v05`：构建中文文本处理工具集，三个阶段
（charfreq → linecount → 整合 README），每阶段带 typed acceptance
checks（file-exists + command-exit-zero），经 goal/prepare 绑定合同。

执行环境：**真实常驻 daemon**（`spawn_daemon` 分离进程，2s tick），
非脚本内 tick——这是与 v4 的关键区别，协议的全部自主行为
（重派/verifier 派生/裁决/升级）由 daemon 自行发生。

## 断裂①（关闭原会话）：走通 ✅

剧本：dsh/MiniMax 执行 stage-1 → 真实起来后 `taskkill /F /T` 硬杀
（协议视角 = 进程消失 + 心跳停止，无优雅退出、无 write-back）。

真实事件链（见 events.jsonl）：

```
lease/acquired + attempt/started（att-1，executor=dsh-headless）
lease/renewed ×n（心跳）
[BREAK-1: taskkill att-1]
attempt/failed（att-1）+ lease/released     ← daemon 观察 ExitCode 1
lease/acquired + attempt/started（att-2）   ← daemon 自动重派
att-2 succeeded（stdout 自证两条 checks）  ← 新会话接力完成
attempt/started（ver-1，role=verifier，executor=dsh-verifier）
  ← executor succeeded 后 verifier 自动派生（v4 是手动 phase2）
ver-1 failed（见发现 5）
[repair 循环] att-3 succeeded + att-4 succeeded（自动）
escalation/handed-to-user（verification budget 1/1 耗尽）
contract blocked(need-user)                 ← 如实升级，不假装
```

交付物 `charfreq.py` + `test_charfreq.py` 真实有效（归档后复跑
`All tests passed!`）。

## 如实记录的发现（dogfood 的核心产出）

1. **（环境）用户主目录 ~/.dsh 的 provider 全超时**，dogfood 隔离的
   dsh-home（MINIMAX_CN_API_KEY）可通——per-executor DSH_HOME 需要
   包装器表达（dsh_executor_wrap.py / dsh_verifier_wrap.py）。
   注册表 argv 无法表达 per-entry 环境覆盖，是适配层通用需求。
2. **（协议缺口）用户没有「直接请求验收」的命令面**：执行者已产出
   有效交付物 + dispatch 预算耗尽时，用户无法说「别再派执行者，
   直接验收」——verifier 只在 executor 报 succeeded 后自动派生。
3. **（协议张力）verification_attempts_reserved 默认 1 太小**：
   一轮外部抖动（kimi provider 超时）就耗尽验证预算，repair 闭环
   饿死。§12.4 独立记账正确，默认值与外部世界稳定性不匹配。
4. **（协议缺口，最重要）CLI 型 verifier 的结论进不了证据通道**：
   `attempt/write-back` RPC 完整存在（verifier 终态强制 evidence 列表
   落 attempt/succeeded 事件），但 dsh 这类 headless 一次性子进程
   verifier 无法调 RPC——它的结论只出现在 stdout，而
   `_judge_verifier_outcomes` 的裁决链不读 stdout 里的结论，只信
   协议自动评估器的 outcome。本例中 deepseek 实际核验通过（它激活
   venv 跑了测试、结论 succeeded），协议却因裸 PATH 无 python 判
   undetermined → verifier failed。需要「CLI 结论 → 结构化证据」
   的桥（如 stdout 协议格式或 verifier 包装器写回）。
5. **（协议缺口）command-exit-zero 的执行环境未定义**：评估器在
   daemon 进程环境跑 `python test_charfreq.py`，裸 PATH 无 python →
   WinError 2 → undetermined。连 verifier 模型都发现并自行解决了
   这个问题（它激活了 venv）。检查声明里的命令需要环境契约
   （解释器路径/PATH 基准）。
6. **（文档缺口）check target 相对 workspace_root 的约定未文档化**：
   阶段声明 `ws/charfreq.py` 会被解析成 `ws/ws/charfreq.py`——
   模型第一次用就踩坑。SKILL.md 应写明。

## 复现

```bash
python examples/dsh-dogfood-v5/dogfood_v5.py setup    # 建 Goal + 3 阶段 + 绑定合同
python examples/dsh-dogfood-v5/dogfood_v5.py stage1   # 真实 daemon + 断裂①注入 + 恢复观察
python examples/dsh-dogfood-v5/dogfood_v5.py stage1-verify  # 验收 stage-1 并记录修复/重验结果
python examples/dsh-dogfood-v5/dogfood_v5.py build-registry  # 仅重建本地执行器注册表
python examples/dsh-dogfood-v5/dogfood_v5.py stage2-plan  # 不启动外部 CLI 的授权预演
python examples/dsh-dogfood-v5/dogfood_v5.py stage2   # kimi CLI 接力 + 独立 verifier
python examples/dsh-dogfood-v5/dogfood_v5.py stage3   # 重启 daemon 后继续 stage-3
python examples/dsh-dogfood-v5/dogfood_v5.py status   # 状态检查
# 前置：.dogfood/dsh-home（minimax key）+ .dogfood/dsh-home-verifier
#（deepseek key，复制后删 profiles/node_modules 让 dsh 重建 symlink）
```

## 文件清单

- `events.jsonl`：2532 个事件完整审计流（含 lease 心跳）；
- `final_state.json`：收尾时 attempts/contracts/goal 快照；
- `charfreq.py` / `test_charfreq.py`：MiniMax 真实交付物；
- `dogfood_v5.py`：驱动脚本（setup/build-registry/stage1/stage1-verify/status）；
- `dsh_executor_wrap.py` / `dsh_verifier_wrap.py`：per-executor
  DSH_HOME 包装器（发现 1 的解法）。

## v5 运行状态（如实声明）

stage2/stage3 驱动脚本现已实现，但需要本机可用的 kimi CLI、DSH provider
凭据与真实运行窗口；代码提交不等于已完成 Alpha 实测。事件与最终快照应在
本地运行后核对，并把结果追加到 `docs/evidence/`。

当执行预算耗尽但工作区疑似已经满足验收时，当前协议优先使用
`longtask request-verification <contract_id>`（或 MCP 的
`lhgp_request_verification`）请求只验收现状，不应立即重新起草合同。
只有该请求因验证预算耗尽、终态或其他明确拒接原因无法执行时，才进入
新合同/人工升级路径；本目录早期归档记录的 workaround 不代表当前首选流程。
