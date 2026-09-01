"""Forecast 模型（SPEC §10.2）。

六分量 + p50/p90/P(finish)：
- queue_minutes:       排队中（与本目标同批的所有合同排到自己之前的剩余时间）
- startup_minutes:     daemon 接到 attempt/started 到 Popen 实际起来的延迟
- remaining_minutes:   剩余工作量（按 workload_initial_hours 估算，可由 handover 滚动更新）
- verification_minutes: verifier 派生 attempt 的工时（§12）
- retry_reserve_minutes: 留作重试的预算（依 budget.max_dispatches 与已消耗估算）
- safety_margin_minutes: 安全余量（Deadline 越线前最后 N 分钟保守）

P4 范围：仅承载 dataclass 与 to_dict；具体计算由 callsite 注入（本阶段
默认 None 留待 daemon/runner 在 attempt/started 后回填）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Forecast:
    """六分量 forecast（SPEC §10.2）。"""

    queue_minutes: float | None = None
    startup_minutes: float | None = None
    remaining_minutes: float | None = None
    verification_minutes: float | None = None
    retry_reserve_minutes: float | None = None
    safety_margin_minutes: float | None = None
    forecast_p50_minutes: float | None = None  # queue + startup + remaining + verification (P50)
    forecast_p90_minutes: float | None = None  # 上述 + retry_reserve + safety_margin (P90)
    p_finish: float | None = None  # 0..1 估计（依 P50 / 剩余时间）

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue_minutes": self.queue_minutes,
            "startup_minutes": self.startup_minutes,
            "remaining_minutes": self.remaining_minutes,
            "verification_minutes": self.verification_minutes,
            "retry_reserve_minutes": self.retry_reserve_minutes,
            "safety_margin_minutes": self.safety_margin_minutes,
            "forecast_p50_minutes": self.forecast_p50_minutes,
            "forecast_p90_minutes": self.forecast_p90_minutes,
            "p_finish": self.p_finish,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Forecast:
        return cls(
            queue_minutes=_opt_float(data.get("queue_minutes")),
            startup_minutes=_opt_float(data.get("startup_minutes")),
            remaining_minutes=_opt_float(data.get("remaining_minutes")),
            verification_minutes=_opt_float(data.get("verification_minutes")),
            retry_reserve_minutes=_opt_float(data.get("retry_reserve_minutes")),
            safety_margin_minutes=_opt_float(data.get("safety_margin_minutes")),
            forecast_p50_minutes=_opt_float(data.get("forecast_p50_minutes")),
            forecast_p90_minutes=_opt_float(data.get("forecast_p90_minutes")),
            p_finish=_opt_float(data.get("p_finish")),
        )


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# 风险六档阈值（SPEC §10.3）—— 本阶段不实现行动绑定，只暴露映射
RISK_TIER_THRESHOLDS: tuple[float, ...] = (0.25, 0.5, 1.0, 1.5, 2.0, 3.0)


def risk_tier(u: float | None) -> int | None:
    """u（remaining_hours / time_left_hours）映射到 0..5 档（§10.3）。None 表示越 Deadline。"""
    if u is None:
        return None
    for idx, threshold in enumerate(RISK_TIER_THRESHOLDS):
        if u < threshold:
            return idx
    return len(RISK_TIER_THRESHOLDS)


__all__ = [
    "RISK_TIER_THRESHOLDS",
    "Forecast",
    "risk_tier",
]
