"""P4 forecast dataclass 单元测试。"""

from __future__ import annotations

import pytest

from longtask.forecast.model import RISK_TIER_THRESHOLDS, Forecast, risk_tier

pytestmark = pytest.mark.unit


class TestForecastRoundTrip:
    def test_to_dict_all_keys(self) -> None:
        f = Forecast(
            queue_minutes=5.0,
            startup_minutes=1.0,
            remaining_minutes=10.0,
            verification_minutes=3.0,
            retry_reserve_minutes=2.0,
            safety_margin_minutes=1.0,
            forecast_p50_minutes=19.0,
            forecast_p90_minutes=22.0,
            p_finish=0.85,
        )
        d = f.to_dict()
        assert set(d.keys()) == {
            "queue_minutes",
            "startup_minutes",
            "remaining_minutes",
            "verification_minutes",
            "retry_reserve_minutes",
            "safety_margin_minutes",
            "forecast_p50_minutes",
            "forecast_p90_minutes",
            "p_finish",
        }
        f2 = Forecast.from_dict(d)
        assert f2 == f

    def test_from_dict_all_none_safe(self) -> None:
        f = Forecast.from_dict({})
        assert f.remaining_minutes is None
        assert f.p_finish is None

    def test_from_dict_handles_garbage_values(self) -> None:
        # 非法值不能炸；当作 None
        f = Forecast.from_dict({"queue_minutes": "not-a-number", "p_finish": [1]})
        assert f.queue_minutes is None
        assert f.p_finish is None


class TestRiskTier:
    def test_risk_tier_thresholds(self) -> None:
        assert len(RISK_TIER_THRESHOLDS) == 6

    @pytest.mark.parametrize(
        "u,expected_tier",
        [
            (0.0, 0),
            (0.24, 0),
            (0.25, 1),
            (0.5, 2),
            (0.99, 2),
            (1.0, 3),
            (1.49, 3),
            (1.5, 4),
            (1.99, 4),
            (2.0, 5),
            (2.99, 5),
            (3.0, 6),  # 越最高档
            (100.0, 6),
        ],
    )
    def test_risk_tier_mapping(self, u: float, expected_tier: int) -> None:
        assert risk_tier(u) == expected_tier

    def test_risk_tier_none_for_past_deadline(self) -> None:
        assert risk_tier(None) is None
