"""升级阶梯决策（DESIGN §6.2 阈值表、§6.3 硬边界、§7 租约、§7.1 分区）。

对应 claim: escalation-ladder-decision（quality/claims.json）。
"""

from __future__ import annotations

import pytest

from longtask.promoter.escalation import decide
from longtask.promoter.urgency import UrgencyTier

pytestmark = pytest.mark.unit

# 基准盘上事实：无租约、预算充足、未停滞
BASE = {
    "lease_alive": False,
    "budget_dispatches_left": 3,
    "budget_escalations_left": 2,
    "estimate_stalled": False,
}


def decide_with(tier: UrgencyTier | None, **overrides: object) -> object:
    kwargs = {**BASE, **overrides}
    return decide(tier, **kwargs)  # type: ignore[arg-type]


class TestDeadlineArbitration:
    def test_none_tier_takes_no_ladder_action(self) -> None:
        # 越 Deadline 走仲裁，不走阶梯（DESIGN §6.2 表下注）
        d = decide_with(None)
        assert d.tier is None
        assert not d.consumes_dispatch
        assert not d.consumes_escalation
        assert "arbitration" in d.reason

    def test_none_tier_ignores_everything_else(self) -> None:
        # 即使预算触顶/租约活着，仲裁路径优先于一切阶梯判定
        d = decide_with(None, lease_alive=True, budget_dispatches_left=0)
        assert d.tier is None


class TestLeaseCap:
    @pytest.mark.parametrize(
        "tier",
        [UrgencyTier.RESPAWN, UrgencyTier.PARALLEL, UrgencyTier.HAND_TO_USER],
    )
    def test_live_lease_caps_at_remind(self, tier: UrgencyTier) -> None:
        # 租约活着只能提醒，不能接管不能加派（DESIGN §7）
        d = decide_with(tier, lease_alive=True)
        assert d.tier == UrgencyTier.REMIND
        assert not d.consumes_dispatch
        assert not d.consumes_escalation

    def test_live_lease_caps_even_when_budget_exhausted(self) -> None:
        # 预算耗尽不构成打断健康执行者的理由；提醒免费照发（§7 + §6.3）
        d = decide_with(UrgencyTier.RESPAWN, lease_alive=True, budget_dispatches_left=0)
        assert d.tier == UrgencyTier.REMIND

    def test_queued_tier_with_live_lease_stays_queued(self) -> None:
        # 档 0 本就不打扰任何人，租约活着不改变它
        d = decide_with(UrgencyTier.QUEUED, lease_alive=True)
        assert d.tier == UrgencyTier.QUEUED


class TestFreeTiers:
    @pytest.mark.parametrize(
        ("tier", "expected"),
        [
            (UrgencyTier.QUEUED, UrgencyTier.QUEUED),
            (UrgencyTier.REMIND, UrgencyTier.REMIND),
            (UrgencyTier.STEER, UrgencyTier.STEER),
        ],
    )
    def test_free_tiers_consume_nothing(self, tier: UrgencyTier, expected: UrgencyTier) -> None:
        d = decide_with(tier)
        assert d.tier == expected
        assert not d.consumes_dispatch
        assert not d.consumes_escalation

    def test_free_tiers_survive_exhausted_budget(self) -> None:
        # 档 0/1/2 免费动作：预算耗尽不阻止它们，也不升档 5
        d = decide_with(UrgencyTier.STEER, budget_dispatches_left=0, budget_escalations_left=0)
        assert d.tier == UrgencyTier.STEER
        assert not d.consumes_dispatch


class TestRespawn:
    def test_respawn_consumes_dispatch(self) -> None:
        # 档 3：另起会话，消耗 1 次 max_dispatches（DESIGN §6.2）
        d = decide_with(UrgencyTier.RESPAWN)
        assert d.tier == UrgencyTier.RESPAWN
        assert d.consumes_dispatch
        assert not d.consumes_escalation

    def test_respawn_with_last_dispatch(self) -> None:
        # 预算恰好剩 1：仍可档 3
        d = decide_with(UrgencyTier.RESPAWN, budget_dispatches_left=1)
        assert d.tier == UrgencyTier.RESPAWN
        assert d.consumes_dispatch

    def test_dispatch_budget_exhausted_hands_to_user(self) -> None:
        # 预算硬边界：无钱可花 → 档 5 交还用户，不无限加码（DESIGN §6.3）
        d = decide_with(UrgencyTier.RESPAWN, budget_dispatches_left=0)
        assert d.tier == UrgencyTier.HAND_TO_USER
        assert not d.consumes_dispatch
        assert not d.consumes_escalation
        assert "budget" in d.reason

    def test_escalations_exhausted_does_not_block_respawn(self) -> None:
        # 档 3 不消耗 escalations：escalations=0 不影响另起会话
        d = decide_with(UrgencyTier.RESPAWN, budget_escalations_left=0)
        assert d.tier == UrgencyTier.RESPAWN


class TestParallel:
    def test_stalled_and_partitionable_goes_parallel(self) -> None:
        # 档 3 后估算连续停滞且可分区 → 档 4（DESIGN §6.2/§7.1）
        d = decide_with(UrgencyTier.RESPAWN, estimate_stalled=True, partitions_allowed=True)
        assert d.tier == UrgencyTier.PARALLEL
        assert d.consumes_dispatch
        assert d.consumes_escalation

    def test_parallel_needs_last_of_both_budgets(self) -> None:
        # 两项预算恰好各剩 1：档 4 仍可行
        d = decide_with(
            UrgencyTier.RESPAWN,
            estimate_stalled=True,
            budget_dispatches_left=1,
            budget_escalations_left=1,
        )
        assert d.tier == UrgencyTier.PARALLEL

    def test_escalations_exhausted_falls_back_to_serial(self) -> None:
        # escalations 触顶：退回档 3 串行换人，不升档 5
        d = decide_with(UrgencyTier.RESPAWN, estimate_stalled=True, budget_escalations_left=0)
        assert d.tier == UrgencyTier.RESPAWN
        assert d.consumes_dispatch
        assert not d.consumes_escalation
        assert "serial" in d.reason

    def test_unpartitionable_falls_back_to_serial(self) -> None:
        # §7.1：无法干净分区的合同不允许档 4，只能串行换人
        d = decide_with(UrgencyTier.RESPAWN, estimate_stalled=True, partitions_allowed=False)
        assert d.tier == UrgencyTier.RESPAWN
        assert d.consumes_dispatch
        assert not d.consumes_escalation
        assert "serial" in d.reason

    def test_stalled_with_no_dispatch_budget_hands_to_user(self) -> None:
        # 停滞 + 无 dispatch 预算：连串行换人都做不了 → 档 5
        d = decide_with(UrgencyTier.RESPAWN, estimate_stalled=True, budget_dispatches_left=0)
        assert d.tier == UrgencyTier.HAND_TO_USER

    def test_not_stalled_stays_respawn(self) -> None:
        # 未停滞：不升档 4（停滞判定只信交接估算，§6.2）
        d = decide_with(UrgencyTier.RESPAWN, estimate_stalled=False)
        assert d.tier == UrgencyTier.RESPAWN
        assert not d.consumes_escalation
