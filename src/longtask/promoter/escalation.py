"""升级阶梯编排（DESIGN §6.2、§6.3、§8.3 多合同公平性）。

阶梯决策的数据流：本模块是单次 tick 视角的纯决策函数，不执行动作、
不读盘。执行动作（notify/followup/steer/spawn）经 adapters 公开接口
发出，由 promoter 循环完成；本模块不 import 具体适配器实现。

输入语义（由调用者装配，全部盘上事实）：
- ``tier``：``classify()`` 的 u 值档位；None 表示已越 Deadline（§6.2，
  直接进入 Deadline 仲裁，不走阶梯）。
- ``estimate_stalled``：交接文件里带 source_attempt_id 的滚动估算连续
  两次未下降（§6.2 档 4 触发条件；它蕴含「档 3 已执行」——停滞判定
  的观察窗口就是档 3 之后，不信模型口头报时）。
- ``partitions_allowed``：合同能否干净分区（§7.1：产出单一文件等
  无法分区的合同不允许档 4，只能串行换人）。

预算硬边界（§6.3）：预算只在阶梯需要花钱的档位（3/4）参与判定；
档 0/1/2 是免费动作，预算耗尽不阻止它们。需要花钱而无钱可花 →
档 5 交还用户，绝不无限加码。

档 5 的另一个触发「档 4 执行后 u 仍 ≥ 1.5」需要升级历史与 u 值，
单次 tick 的纯函数拿不到，属 promoter 循环职责，本模块不实现。
"""

from __future__ import annotations

from dataclasses import dataclass

from longtask.promoter.urgency import UrgencyTier


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    """一次推动决策：做什么档、为什么、消耗什么预算。

    ``tier`` 为 None 表示不走阶梯（已越 Deadline，转仲裁路径，
    DESIGN §6.2 分档阈值表注）。
    """

    tier: UrgencyTier | None
    reason: str
    consumes_dispatch: bool  # 档 3/4 消耗 max_dispatches
    consumes_escalation: bool  # 档 4 另消耗 max_escalations


def _free(tier: UrgencyTier, reason: str) -> EscalationDecision:
    """档 0/1/2：免费动作，不消耗任何预算。"""
    return EscalationDecision(
        tier=tier, reason=reason, consumes_dispatch=False, consumes_escalation=False
    )


def decide(
    tier: UrgencyTier | None,
    *,
    lease_alive: bool,
    budget_dispatches_left: int,
    budget_escalations_left: int,
    estimate_stalled: bool,
    partitions_allowed: bool = True,
) -> EscalationDecision:
    """阶梯决策（DESIGN §6.2 阈值表 + §6.3 硬边界 + §7 租约 + §7.1 分区）。

    判定顺序（先钉死的约束优先）：

    1. 越 Deadline → 不走阶梯（None），仲裁路径接管。
    2. 租约活着 → 封顶档 1 提醒。提醒免费，预算耗尽也不构成接管
       理由（§7：健康租约持有者正在推进，blocked 会误伤进行中的
       attempt；预算触顶在租约消亡后的下一轮 tick 兑现）。
    3. 档 0/1/2 → 原档执行，免费。
    4. 档 3/4（要花钱的档位）：
       - 预算 dispatch 触顶 → 档 5 交还用户（§6.3）。
       - 停滞且可分区且两项预算都够 → 档 4 并行加派。
       - 停滞但不可分区（§7.1）或 escalations 触顶 → 退回档 3 串行换人。
       - 其余 → 档 3 另起会话。
    """
    if tier is None:
        return EscalationDecision(
            tier=None,
            reason="past deadline: contract goes to arbitration, not the ladder (§6.2)",
            consumes_dispatch=False,
            consumes_escalation=False,
        )

    if lease_alive:
        # 封顶是「不高于档 1」，不是抬到档 1：排队中的合同仍不打扰（§6.2 档 0）
        capped = min(tier, UrgencyTier.REMIND)
        return _free(
            capped,
            f"lease alive: cap at remind (§7), u-tier {int(tier)} -> {int(capped)}",
        )

    if tier in (UrgencyTier.QUEUED, UrgencyTier.REMIND, UrgencyTier.STEER):
        return _free(tier, f"u-tier {int(tier)}: free action, no budget consumed")

    # 档 3 起步：要花钱，先看预算硬边界（§6.3）
    if budget_dispatches_left < 1:
        return EscalationDecision(
            tier=UrgencyTier.HAND_TO_USER,
            reason="dispatch budget exhausted: hand to user (§6.3)",
            consumes_dispatch=False,
            consumes_escalation=False,
        )

    # 档 4：档 3 执行后估算连续停滞（estimate_stalled 蕴含档 3 已执行）
    if estimate_stalled:
        if partitions_allowed and budget_escalations_left >= 1:
            return EscalationDecision(
                tier=UrgencyTier.PARALLEL,
                reason="estimate stalled after tier 3 and partitionable: parallel "
                "dispatch (§6.2/§7.1)",
                consumes_dispatch=True,
                consumes_escalation=True,
            )
        # 不可分区（§7.1）或 escalations 触顶 → 串行换人
        return EscalationDecision(
            tier=UrgencyTier.RESPAWN,
            reason="estimate stalled but tier 4 unavailable "
            f"(partitions_allowed={partitions_allowed}, "
            f"escalations_left={budget_escalations_left}): serial respawn (§7.1)",
            consumes_dispatch=True,
            consumes_escalation=False,
        )

    return EscalationDecision(
        tier=UrgencyTier.RESPAWN,
        reason="u >= respawn threshold and no live lease: dispatch new attempt (§6.2)",
        consumes_dispatch=True,
        consumes_escalation=False,
    )
