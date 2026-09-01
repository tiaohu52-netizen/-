"""事件类型（DESIGN §4.1、§5、§11.3、§7 四轴事件）。

事件是数据库事务的一部分：状态快照、事件记录和幂等键在同一事务中提交。
本模块只定义事件类型词汇表；写入路径在 store.py。
"""

from enum import StrEnum


class EventType(StrEnum):
    """协议事件全集。新增只能追加，不得改既有语义（DESIGN §11.7 同源纪律）。"""

    # 合同生命周期（DESIGN §5、§7 commitment lifecycle 轴）
    CONTRACT_PREPARED = "contract/prepared"
    CONTRACT_APPROVED = "contract/approved"
    CONTRACT_PATCHED = "contract/patched"
    CONTRACT_PAUSED = "contract/paused"
    CONTRACT_RESUMED = "contract/resumed"
    CONTRACT_CANCELLED = "contract/cancelled"
    CONTRACT_EXPIRED = "contract/expired"
    CONTRACT_BLOCKED = "contract/blocked"
    CONTRACT_COMPLETED = "contract/completed"  # legacy alias
    CONTRACT_SATISFIED = "contract/satisfied"  # P1：acceptance_status=satisfied 推导的终态
    CONTRACT_ARBITRATED = "contract/arbitrated"

    # P1：合同修订快照（不可变 contract_revisions 表的同源事件）
    CONTRACT_REVISION = "contract/revision"

    # attempt 生命周期（DESIGN §5.1、§7 attempt 轴）
    ATTEMPT_ADMITTED = "attempt/admitted"
    ATTEMPT_STARTED = "attempt/started"
    ATTEMPT_SUCCEEDED = "attempt/succeeded"
    ATTEMPT_FAILED = "attempt/failed"
    ATTEMPT_CANCELLED = "attempt/cancelled"
    ATTEMPT_STALE = "attempt/stale"
    ATTEMPT_ORPHANED = "attempt/orphaned"  # P3：daemon 重启后无人认领

    # deadline_status / acceptance_status 轴变更事件（P1：四轴独立事件词汇）
    DEADLINE_STATUS_CHANGED = "deadline/status-changed"
    ACCEPTANCE_STATUS_CHANGED = "acceptance/status-changed"

    # 决策（DESIGN §6、P1：decision 实体表同源事件）
    DECISION_RECORDED = "decision/recorded"

    # 租约（DESIGN §7）
    LEASE_ACQUIRED = "lease/acquired"
    LEASE_RENEWED = "lease/renewed"
    LEASE_RELEASED = "lease/released"
    LEASE_RECLAIMED = "lease/reclaimed"
    LEASE_FENCED = "lease/fenced"
    LEASE_PARTITION_CONFLICT = "lease/partition-conflict"

    # 上下文（DESIGN §4.1）
    CONTEXT_POLICY_APPROVED = "context/policy-approved"
    CONTEXT_SNAPSHOT_BUILT = "context/snapshot-built"
    CONTEXT_SNAPSHOT_EXPIRED = "context/snapshot-expired"
    CONTEXT_SCRATCH_UPDATED = "context/scratch-updated"
    CONTEXT_PROMOTION_REQUESTED = "context/promotion-requested"
    CONTEXT_PROMOTION_ACCEPTED = "context/promotion-accepted"
    CONTEXT_PROMOTION_REJECTED = "context/promotion-rejected"
    CONTEXT_CAPACITY_REFUSED = "context/capacity-refused"
    CONTEXT_REBUILT = "context/rebuilt"

    # 交接与投影（DESIGN §3.1）
    HANDOVER_INCOMPLETE = "handover/incomplete"
    PROJECTION_REBUILT = "projection/rebuilt"
    PROJECTION_DIRTY = "projection/dirty"
    STORE_TAMPERED = "store/tampered"

    # 推动层（DESIGN §6）
    ESCALATION_REMINDED = "escalation/reminded"
    ESCALATION_STEERED = "escalation/steered"
    ESCALATION_SPAWNED = "escalation/spawned"
    ESCALATION_PARALLELIZED = "escalation/parallelized"
    ESCALATION_HANDED_TO_USER = "escalation/handed-to-user"
    DISPATCH_REFUSED = "dispatch/refused"

    # P3：连续性闭环（外部句柄 / reconcile / checkpoint / capsule）
    HANDLE_REGISTERED = "handle/registered"  # spawn 持久化外部句柄
    RECONCILE_REATTACHED = "reconcile/reattached"
    RECONCILE_COLLECTED = "reconcile/collected"
    RECONCILE_ORPHAN_GRACED = "reconcile/orphan-graced"
    RECONCILE_FENCED_REDISPATCHED = "reconcile/fenced-redispatched"
    CHECKPOINT_BUILT = "checkpoint/built"
    CAPSULE_BUILT = "capsule/built"

    # P4：forecast / outbox / next_decision_at
    FORECAST_UPDATED = "forecast/updated"
    NEXT_DECISION_AT_SET = "next-decision/set"
    NOTIFY_DISPATCHED = "notify/dispatched"

    # 分层唤醒（DESIGN §6.4、ADR-0002）：唤醒源不是权威，只推不裁
    WAKEUP_SLEEP_GUARD = "wakeup/sleep-guard"  # L0 持有/释放电源请求
    WAKEUP_RTC_ARMED = "wakeup/rtc-armed"  # L1 计划任务唤醒注册
    WAKEUP_RTC_FIRED = "wakeup/rtc-fired"  # L1 唤醒信号到点
    WAKEUP_DEGRADED = "wakeup/degraded"  # 任一层失效：降级声明，不静默
