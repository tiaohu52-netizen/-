"""ticker 与三种时间（DESIGN §3.3、§6.4）。

调度器必须区分三种时间，本模块用数据类把这个区分钉死在类型里：
- deadline_at：合同声明的墙钟截止点（来自合同冻结区）
- next_wakeup_at：下一次推动检查点（调度器自己算）
- arbitrated_at：实际做出裁决的时间（ticker 醒来扫描时的墙钟）

睡眠/重启/关机期间不伪造「已经推进」；恢复后只根据当前墙钟和盘上状态
做一次可回放的仲裁（仲裁时刻语义）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

# ticker 扫描间隔（DESIGN §10 沿用 Hermes cron 的 60s 骨架）
DEFAULT_TICK_SECONDS = 60


@dataclass(frozen=True, slots=True)
class ContractClock:
    """一个合同在调度器眼里的三种时间（DESIGN §3.3）。"""

    deadline_at: datetime  # 必须带显式时区（ContractDraft.validate 已强制）
    next_wakeup_at: datetime
    arbitrated_at: datetime | None  # 尚未仲裁为 None


@dataclass(frozen=True, slots=True)
class ClockEntry:
    """ticker 单轮扫描的一个条目（DESIGN §3.3）：合同号 + 该合同的三种时间。

    ticker 不持有合同本体，只拿合同号与三种时间做纯判定；合同号用于
    emit 事件的 ``<id>`` 后缀（如 ``contract/expired:lt-20260831-001``）。
    """

    contract_id: str
    clock: ContractClock


def is_overdue(clock: ContractClock, now: datetime) -> bool:
    """仲裁时刻语义（DESIGN §6.4）：以当前墙钟判定是否越过 Deadline。

    纯函数，时间全部注入（CONTRIBUTING「测试纪律」）。
    """
    return now > clock.deadline_at


def next_wakeup(clock: ContractClock, now: datetime) -> datetime:
    """计算下一次唤醒点。骨架：直接返回已声明的 next_wakeup_at。

    Developer Preview：按紧迫档冷却约束（DESIGN §6.2 分档阈值表）重算。
    """
    return clock.next_wakeup_at


def run_tick(
    now: datetime,
    entries: Sequence[ClockEntry],
    emit: Callable[[str], None],
) -> tuple[ClockEntry, ...]:
    """单轮扫描（DESIGN §3.3、§5、§6.4）。

    ticker 只做三件事：扫合同状态、盯 Deadline、决定何时触发推动层——
    本函数是它的纯函数内核：不改入参、不执行任务、时间全部注入；
    唤醒本身属于守护进程外围（DESIGN §6.4 分层唤醒）。

    逐合同判定，规则按优先级：
    1. now > deadline_at 且 arbitrated_at 为 None → 判过期：emit
       ``contract/expired:<id>``，返回 arbitrated_at=now 的新时钟。
       expired 不硬停、保中间成果、转人工裁决（DESIGN §5）。
    2. 已 arbitrated 的过期合同不再重复判过期（睡眠/关机期间不伪造
       「已经推进」，DESIGN §6.4），也不再触发推动（DESIGN §5.1：
       expired 后不得再启动普通 attempt）；时钟原样返回。
    3. 未过期且 next_wakeup_at ≤ now → emit ``promote:<id>``：本层只
       决定「何时」，绝不执行「怎么推」；时钟原样返回，下一次唤醒点
       由推动层行动后重算（DESIGN §6.2 分档冷却）。
    4. 其余（next_wakeup_at 在未来）不动，时钟原样返回。

    now == deadline_at 不算过期：仲裁时刻语义按严格大于判定（DESIGN §6.4）。
    返回值与入参同序同长；未被动过的条目原样透传。
    """
    results: list[ClockEntry] = []
    for entry in entries:
        clock = entry.clock
        if not is_overdue(clock, now):
            if clock.next_wakeup_at <= now:
                emit(f"promote:{entry.contract_id}")
            results.append(entry)
        elif clock.arbitrated_at is None:
            emit(f"contract/expired:{entry.contract_id}")
            results.append(
                ClockEntry(
                    contract_id=entry.contract_id,
                    clock=replace(clock, arbitrated_at=now),
                )
            )
        else:
            results.append(entry)
    return tuple(results)
