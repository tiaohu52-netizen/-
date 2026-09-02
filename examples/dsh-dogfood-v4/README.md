# DSH+Kimi 双 CLI × 双模型真实考验（dogfood v4）：完整甜路径走通

日期：2026-09-02。本目录是 v4 归档——相对 v3，这次把**两个真实 CLI、
两个模型家族**放进同一份合同的授权矩阵，验证协议在多执行器选择、
default-deny、跨家族交叉验收下的真实行为。

## 用户决定（虚假用户信息，如实标注为演示数据）

- 注册 **dsh-headless** 与 **kimi-code** 两个 CLI 执行器；后端模型池覆盖
  **MiniMax / kimi-k3（月之暗面）/ deepseek**（dsh 侧 settings.yaml 可切
  x5anci/deepseek-v4-pro，已实测可用；kimi 侧 x5m 后端含
  MiniMax-M2.7-highspeed / deepseek-v4-pro 别名）；
- 合同 authority 显式框定：
  - `dsh-headless`（models: MiniMax-M2.7-highspeed）→ 只当 **executor**；
  - `kimi-code`（models: kimi-k3）→ 只当 **verifier**；
  - 注册表里另有干扰项 `codex-cli`（enabled，未授权）。

## 真实走通的考验链

1. **default-deny（probe）**：executor 视角候选只有 `dsh-headless`、
   verifier 视角只有 `kimi-code`、`codex-cli` 全拒——合同 authority 的
   角色矩阵在两个真实 CLI 上正确强制（commit `22b91c1` 的分发面强制）。
2. **executor 选举**：tick 派工只选 `dsh-headless`（不是 cost 更低的
   codex-cli）——授权优先于成本排序。
3. **真实干活**：dsh/MiniMax-M2.7-highspeed 写 `wordcount.py` +
   `tests_wc.py` 并自测（`test_output.txt`: All tests passed!）。
4. **跨家族交叉验收（甜路径）**：kimi/kimi-k3 作为独立 verifier 核对
   交付物 → `attempt/succeeded`（returncode 0）→ 裁决
   `contract/completed`（actor=verifier，带 stdout 证据）→
   合同状态 **complete**。
   - 执行者 MiniMax（沪）、验收者 kimi-k3（月之暗面）——不同 CLI、
     不同模型家族，§5.2 交叉核对的真实形态。

## 事件链（82 个事件，完整审计流见 events.jsonl）

```
contract/prepared → contract/approved（authority 矩阵进冻结区）
lease/acquired + attempt/started + handle/registered（executor=dsh）
lease/renewed ×n（心跳）
context/snapshot-built（verifier 的 active.md 含验收条款锚点）
lease/acquired + attempt/started + handle/registered（verifier=kimi）
attempt/succeeded（verifier returncode=0，stdout_tail 带核对报告）
lease/released
contract/completed ×2（裁决事件带证据 + 状态迁移记录）
```

## 如实记录的发现与瑕疵

1. **kimi CLI 的参数结构与协议 spawn 约定不兼容**：协议把 task_prompt
   作为 argv 尾元素追加，kimi 的 `[options] [command]` 结构把它当子命令
   （`unknown command '你是 verifier...'`）。解法：`kimi_wrap.py` 包装器
   把尾元素转成 `-p` 的值。**这暴露了适配层的一个通用需求**：CLI 参数
   结构差异（tail-append vs flag-value）应由包装器吸收，注册表 argv
   保持结构化。
2. **kimi x5m 后端与 -p 一次性模式不兼容**（400 参数错误）——故
   verifier 用 kimi 原生 managed 后端（kimi-k3）。deepseek 后端在 dsh
   侧可用。多模型覆盖以「每个 CLI 用其可用后端」的如实形态达成。
3. **kimi config 的 `default_plan_mode = true`**：verifier 会话在 plan
   mode 启动，模型自述「I'm in plan mode — no code changes allowed」
   并把部分注意力放在 plan-mode 框架上（报告里称「未收到具体验收条款
   文本」——prompt 透传已单独验证无缺失，属模型自谦/注意漂移；它实际
   读了工作区文件并准确核对）。对只读的 verifier 无行为损害，但
   prompt 效力被削弱——生产上应在 wrapper 加 `--plan=false` 类覆盖。
4. **phase1 的 tick verifier 派生尝试被拒**（`escalation/handed-to-user:
   no independent verifier candidate`）：executor 还在 running 时 tick
   的 `_dispatch_verifier` 判定失败（候选筛选的时序边界），如实记事件
   后 phase2 直调成功。非阻塞瑕疵。
5. 两次 `contract/completed` 语义不同（裁决证据 vs 状态迁移），非重复。

## 文件清单

- `events.jsonl`：82 个事件完整审计流；
- `wordcount.py` / `tests_wc.py` / `test_output.txt`：MiniMax 真实交付物
  与自测输出；
- `dogfood_v4.py`：实测驱动脚本（probe/phase1/phase2 三阶段）；
- `kimi_wrap.py`：kimi CLI 适配包装器（argv 尾元素 → -p 值）。

## 复现

```bash
python .dogfood/dogfood_v4.py probe    # default-deny 候选矩阵验证（不跑 LLM）
python .dogfood/dogfood_v4.py phase1   # 立合同+派工 dsh/MiniMax+观察
python .dogfood/dogfood_v4.py phase2   # 派 kimi/kimi-k3 verifier+裁决
# 前置：MINIMAX_CN_API_KEY（dsh 后端）+ 临时 DSH_HOME（默认模型切
# MiniMax-M2.7-highspeed）
```
