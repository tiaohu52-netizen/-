"""Forecast model (SPEC §10.2).

The canonical ``lhgp`` namespace owns this implementation.  The historical
``longtask.forecast.model`` path remains a compatibility facade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Forecast:
    """Six-component forecast with p50/p90 and finish probability."""

    queue_minutes: float | None = None
    startup_minutes: float | None = None
    remaining_minutes: float | None = None
    verification_minutes: float | None = None
    retry_reserve_minutes: float | None = None
    safety_margin_minutes: float | None = None
    forecast_p50_minutes: float | None = None
    forecast_p90_minutes: float | None = None
    p_finish: float | None = None

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


RISK_TIER_THRESHOLDS: tuple[float, ...] = (0.25, 0.5, 1.0, 1.5, 2.0, 3.0)


def risk_tier(u: float | None) -> int | None:
    """Map urgency ratio ``u`` to tier 0..5; None means past deadline."""
    if u is None:
        return None
    for idx, threshold in enumerate(RISK_TIER_THRESHOLDS):
        if u < threshold:
            return idx
    return len(RISK_TIER_THRESHOLDS)


__all__ = ["RISK_TIER_THRESHOLDS", "Forecast", "risk_tier"]
