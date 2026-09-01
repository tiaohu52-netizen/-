"""紧迫度与分档阈值（DESIGN §6.1、§6.2 分档阈值表）。

urgency = 剩余工作量估算(小时) ÷ 剩余时间(小时)

阈值与冷却是全局配置（config.yaml 默认），合同级不可调——
防止立约时「谈一个更急的梯子」架空预算纪律（DESIGN §6.2）。
本模块是纯函数实现，时间全部注入，驱动 conformance/unit 测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class UrgencyTier(IntEnum):
    """升级阶梯档位（DESIGN §6.2）。IntEnum 保证可比较、可排序。"""

    QUEUED = 0  # 排队：不打扰任何人
    REMIND = 1  # 提醒：向持约会话注入倒计时提醒
    STEER = 2  # 转向：把话题扳回合同任务
    RESPAWN = 3  # 另起会话：headless 拉起新执行者
    PARALLEL = 4  # 并行加派：分区租约多执行者
    HAND_TO_USER = 5  # 升级到人：blocked(need-user)


@dataclass(frozen=True, slots=True)
class UrgencyThresholds:
    """分档阈值（DESIGN §6.2 默认值，来自 config.yaml）。

    u 值区间：[0, remind) 排队；[remind, steer) 提醒；[steer, respawn) 转向；
    ≥ respawn 另起会话。剩余时间 ≤ 0 不走阶梯，直接 Deadline 仲裁。
    """

    remind: float = 0.25
    steer: float = 0.5
    respawn: float = 1.0
    # 档 4→5 的停滞判定线：档 4 执行后 u 仍 ≥ 此值则升级到人
    hand_to_user: float = 1.5

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not (0 < self.remind < self.steer < self.respawn < self.hand_to_user):
            errors.append(
                "thresholds must satisfy 0 < remind < steer < respawn < hand_to_user, "
                f"got {self.remind}/{self.steer}/{self.respawn}/{self.hand_to_user}"
            )
        return errors


DEFAULT_THRESHOLDS = UrgencyThresholds()


def urgency(remaining_hours: float, hours_left: float) -> float | None:
    """紧迫度公式（DESIGN §6.1）。

    剩余时间 ≤ 0 返回 None：合同进入 Deadline 仲裁，不再走阶梯。
    """
    if hours_left <= 0:
        return None
    if remaining_hours < 0:
        raise ValueError(f"remaining_hours must be >= 0, got {remaining_hours}")
    return remaining_hours / hours_left


def classify(
    u: float | None,
    thresholds: UrgencyThresholds = DEFAULT_THRESHOLDS,
) -> UrgencyTier | None:
    """把 u 值归入档位。u 为 None（已越 Deadline）返回 None：走仲裁不走阶梯。

    档 3→4→5 的推进还需要停滞判定与预算检查（DESIGN §6.2），
    那些依赖交接估算历史的判定在 escalation.py；本函数只回答「按 u 值
    此刻至少该在哪一档」。
    """
    if u is None:
        return None
    if u < thresholds.remind:
        return UrgencyTier.QUEUED
    if u < thresholds.steer:
        return UrgencyTier.REMIND
    if u < thresholds.respawn:
        return UrgencyTier.STEER
    return UrgencyTier.RESPAWN
