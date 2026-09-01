"""工作租约与 fencing（DESIGN §7、§7.1）。

权威状态在 state.db 事务中变更（CAS：expected_generation，冲突即失败重读）；
本模块实现 fencing 判定与分区互斥校验的纯逻辑，事务壳在 persistence 层。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


class LeaseFencedError(Exception):
    """写回携带过期 lease_generation，对应线协议错误码 LEASE_FENCED。"""


@dataclass(frozen=True, slots=True)
class Lease:
    """租约（DESIGN §7）。

    generation 单调递增：每次租约变更（获取/回收/换人）+1。
    执行者的一切写回必须携带自己的 attempt_id 与 lease_generation。
    """

    contract_id: str
    holder_attempt_id: str
    generation: int
    heartbeat_at: datetime
    timeout: timedelta
    partition_id: str | None = None  # 整合同租约为 None（DESIGN §7.1）

    def is_alive(self, now: datetime) -> bool:
        """心跳存活判定。纯函数，时间注入。"""
        return now <= self.heartbeat_at + self.timeout


def check_write_fence(lease: Lease, write_generation: int, write_attempt_id: str) -> None:
    """旧执行者隔离（DESIGN §7 fencing 与 §11.3）。

    写回 generation 不等于当前租约 generation → LEASE_FENCED 丢弃，
    绝不污染新 attempt 状态。覆盖「A 卡死 → 租约回收 → B 接管 → A 苏醒
    继续写」的竞态；同 generation 但 attempt_id 不符同样拒绝。
    """
    if write_generation != lease.generation:
        raise LeaseFencedError(
            f"write generation {write_generation} fenced by lease generation "
            f"{lease.generation} (contract {lease.contract_id})"
        )
    if write_attempt_id != lease.holder_attempt_id:
        raise LeaseFencedError(
            f"write attempt {write_attempt_id} is not lease holder "
            f"{lease.holder_attempt_id} (contract {lease.contract_id})"
        )


@dataclass(frozen=True, slots=True)
class Partition:
    """分区（DESIGN §7.1）：路径与阶段互斥的子工作面。"""

    partition_id: str
    scope_paths: tuple[str, ...]  # 允许写入的路径前缀（已归一化）
    scope_stages: tuple[str, ...]
    description: str = ""


def _path_prefix_overlap(a: str, b: str) -> bool:
    """归一化前缀的交集判定：a 是 b 的前缀或反之即重叠。"""
    a_norm = a.rstrip("/")
    b_norm = b.rstrip("/")
    return a_norm == b_norm or a_norm.startswith(b_norm + "/") or b_norm.startswith(a_norm + "/")


def check_partition_compatible(new: Partition, active: list[Partition]) -> list[str]:
    """新分区与活跃分区的互斥校验（DESIGN §7.1 互斥判定）。

    返回违规说明列表，空列表表示可加派；调用方负责记录
    lease/partition-conflict 事件并拒绝加派。
    """
    errors: list[str] = []
    for other in active:
        if other.partition_id == new.partition_id:
            errors.append(f"partition id already active: {new.partition_id}")
        for pa in new.scope_paths:
            for pb in other.scope_paths:
                if _path_prefix_overlap(pa, pb):
                    errors.append(
                        f"scope_paths overlap with partition {other.partition_id}: {pa} vs {pb}"
                    )
        shared_stages = set(new.scope_stages) & set(other.scope_stages)
        if shared_stages:
            errors.append(
                f"scope_stages overlap with partition {other.partition_id}: {sorted(shared_stages)}"
            )
    return errors
