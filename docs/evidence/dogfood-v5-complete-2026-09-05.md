# Dogfood v5 完整验收证据（2026-09-05）

本次运行使用真实 daemon、真实 dsh verifier、真实 Kimi executor，未使用 fake
executor。运行目录为 `.dogfood-v5`；目录本身是可归档的运行产物，不纳入源码提交。

## 最终状态

```text
goal lt-dogfood-v05
revision=7
progress.completed=[stage-1, stage-2, stage-3]
progress.current=null
progress.status=satisfied

lt-dogfood-v05          satisfied / passed
lt-dogfood-v05-stage2   satisfied / passed
lt-dogfood-v05-stage3   satisfied / passed
```

## 关键 attempt 链

| 阶段 | attempt | 执行器 | 结果 |
|---|---|---|---|
| stage-1 | `att-20260905013036--v05` | dsh-headless | failed（注入断裂） |
| stage-1 | `att-20260905013145--v05` | dsh-headless | succeeded |
| stage-1 | `ver-20260905013425--v05` | dsh-verifier | succeeded |
| stage-2 | `att-20260905013847-age2` | kimi-code | succeeded |
| stage-2 | `ver-20260905013940-age2` | dsh-verifier | failed（真实验收失败） |
| stage-2 | `att-20260905015520-age2` | kimi-code | failed |
| stage-2 | `att-20260905015622-age2` | kimi-code | succeeded |
| stage-2 | `att-20260905015802-age2` | kimi-code | failed（预算耗尽边界） |
| stage-2 | `ver-20260905020729-age2` | dsh-verifier | succeeded（用户触发验收） |
| stage-3 | `att-20260905021931-age3` | dsh-headless | succeeded |
| stage-3 | `ver-20260905022000-age3` | dsh-verifier | succeeded |

stage-2 的关键闭环是：verifier failure → repair brief → 多次 Kimi repair →
executor budget exhausted → `request-verification` → 独立 verifier → complete。
中途 daemon 被停止并重启，孤儿 attempt 被 reconcile；stage-3 随后再次验证了
重启后的 Goal、合同和事件流连续性。

## 真实交付物

运行 workspace 最终包含：

- `charfreq.py` / `test_charfreq.py`
- `linecount.py` / `test_linecount.py`
- `README_tools.md`

所有三个阶段的 mandatory acceptance checks 均已通过，最终 daemon 已停止。

## 本次运行额外验证的协议性质

- 用户验收请求只派 verifier，不重新派 executor。
- verifier 预算按 `contract_id` 隔离；stage-1 不会消耗 stage-2 配额。
- `max_attempt_minutes` 到期会取消 attempt、写入 `attempt-timeout` 并释放租约。
- daemon 重启后可恢复外部句柄、收敛孤儿 attempt，并继续阶段推进。
