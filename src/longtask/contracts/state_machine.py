"""合同状态机与合法迁移规则（DESIGN §5、§7 四轴）。

P1 起把状态机拆为四条独立轴：
- commitment lifecycle（ContractState）：drafted → active → … → terminal
- deadline_status（DeadlineStatus）：on_track / at_risk / past_deadline / waived
- acceptance_status（AcceptanceStatus）：pending / partial / satisfied / failed / not_required
- attempt state（AttemptState）：执行者侧状态

LEGAL_TRANSITIONS 描述 commitment lifecycle 轴；其余三轴在 runtime 由各自
判定函数校验（与契约事件同源）。这是 SPEC §7「四轴独立」的具体落地。

纯函数与状态集合定义，零业务依赖（CONTRIBUTING「模块边界约束」）。
"""

from __future__ import annotations

from longtask.contracts.schema import (
    AcceptanceStatus,
    ContractState,
    DeadlineStatus,
)

# commitment lifecycle 终态集合（DESIGN §5）：进入后不再接受任何常规迁移
# P1：complete 保留作 legacy 字面值；新写法请走 satisfied（§7 acceptance_status 推导）。
TERMINAL_STATES: frozenset[ContractState] = frozenset(
    {
        ContractState.COMPLETE,
        ContractState.SATISFIED,
        ContractState.CANCELLED,
        ContractState.ARCHIVED,
    }
)

# 非终态集合（DESIGN §5、§7）
NON_TERMINAL_STATES: frozenset[ContractState] = frozenset(
    {
        ContractState.DRAFTED,
        ContractState.ACTIVE,
        ContractState.PAUSED,
        ContractState.BLOCKED,
        ContractState.EXPIRED,
    }
)

# 合法迁移图（DESIGN §5 状态机 + P1 §7 终态收敛）
LEGAL_TRANSITIONS: dict[ContractState, frozenset[ContractState]] = {
    ContractState.DRAFTED: frozenset(
        {
            ContractState.ACTIVE,  # contract/approve
            ContractState.CANCELLED,  # contract/cancel
        }
    ),
    ContractState.ACTIVE: frozenset(
        {
            ContractState.PAUSED,  # contract/pause
            ContractState.BLOCKED,  # execution blocked
            ContractState.SATISFIED,  # P1：acceptance 全通过（§7 acceptance 轴推导）
            ContractState.COMPLETE,  # legacy alias
            ContractState.EXPIRED,  # deadline 越线
            ContractState.CANCELLED,  # contract/cancel
        }
    ),
    ContractState.PAUSED: frozenset(
        {
            ContractState.ACTIVE,  # contract/resume
            ContractState.CANCELLED,  # contract/cancel
        }
    ),
    ContractState.BLOCKED: frozenset(
        {
            ContractState.ACTIVE,  # contract/resume / re-dispatch
            ContractState.SATISFIED,  # P1：仲裁后采纳
            ContractState.COMPLETE,  # legacy alias
            ContractState.ARCHIVED,  # contract/arbitrate (作废)
            ContractState.EXPIRED,  # deadline expiry
            ContractState.CANCELLED,  # contract/cancel
        }
    ),
    ContractState.EXPIRED: frozenset(
        {
            ContractState.SATISFIED,  # P1：仲裁采纳部分成果
            ContractState.COMPLETE,  # legacy alias
            ContractState.ARCHIVED,  # contract/arbitrate (作废)
            ContractState.ACTIVE,  # contract/arbitrate (延期/重新激活)
            ContractState.CANCELLED,  # contract/cancel
        }
    ),
    ContractState.SATISFIED: frozenset(),  # 终态无出边
    ContractState.COMPLETE: frozenset(),  # legacy 终态无出边
    ContractState.CANCELLED: frozenset(),  # 终态无出边
    ContractState.ARCHIVED: frozenset(),  # 终态无出边
}


def is_terminal_state(state: ContractState) -> bool:
    """判定给定状态是否为终态（DESIGN §5、§7）。纯函数。"""
    return state in TERMINAL_STATES


def is_valid_transition(from_state: ContractState, to_state: ContractState) -> bool:
    """判定从 from_state 迁移至 to_state 是否合法（DESIGN §5）。纯函数。"""
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


def is_valid_deadline_transition(
    from_status: DeadlineStatus,
    to_status: DeadlineStatus,
    *,
    deadline_passed: bool,
) -> bool:
    """deadline_status 轴迁移合法性（SPEC §7.2，P2）。

    关键不变量（SPEC §7.2）：
    - at_risk 可恢复为 not_due（历史风险事件不删除，但状态轴允许回退）；
    - 在 due_at 前 acceptance passed → met；
    - now > due_at 且未 passed → missed（即使守护进程刚从关机恢复）；
    - 用户主动放弃 → waived，不得伪装成 met。
    """

    # 用户主动 waive：任何状态都可单向进入 waived
    if to_status == DeadlineStatus.WAIVED:
        return True
    # 越线后：只能进入 missed，不可回退 not_due/at_risk
    if deadline_passed and to_status in (
        DeadlineStatus.NOT_DUE,
        DeadlineStatus.AT_RISK,
    ):
        return False
    # 不允许从 missed 回退 not_due/at_risk（仅可 waived）
    return not (
        from_status == DeadlineStatus.MISSED
        and to_status in (DeadlineStatus.NOT_DUE, DeadlineStatus.AT_RISK)
    )


def is_valid_acceptance_transition(
    from_status: AcceptanceStatus,
    to_status: AcceptanceStatus,
) -> bool:
    """acceptance_status 轴迁移合法性（SPEC §7.3，P2）。

    严格按 SPEC §7.3 字面值（pending → candidate → verifying → passed |
    failed | undetermined）。
    - passed 是终态，不可回退；
    - failed 可来自 candidate/verifying/pending（verifier 拒），进入 repair
      round 后可回到 pending（P5）；
    - undetermined 默认使合同 blocked(need-arbitration)；
    - not_required 可从任何非终态进入（acceptance.verifier=none）。
    """

    if from_status == to_status:
        return True
    # passed 是 acceptance 轴终态，不可回退
    if from_status == AcceptanceStatus.PASSED:
        return False
    # not_required 单向设置（任何非终态都可进入）
    if to_status == AcceptanceStatus.NOT_REQUIRED:
        # 上文已拦截 from_status == PASSED；此处只剩非终态
        return True
    # failed 可来自 pending/candidate/verifying（verifier 拒）
    if to_status == AcceptanceStatus.FAILED:
        return from_status in (
            AcceptanceStatus.PENDING,
            AcceptanceStatus.CANDIDATE,
            AcceptanceStatus.VERIFYING,
        )
    # 其它过渡由 write 路径直接覆盖；这里只挡明显不合法的回退
    return False
