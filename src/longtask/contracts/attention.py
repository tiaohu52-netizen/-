"""Attention 字段（SPEC §6.1 attention + §10.5 通知策略）。

P2 起独立模块。承载字段与最小校验；通知 outbox 已在
``longtask.persistence.notifications`` 实现，渠道适配仍由运行时注入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# SPEC §10.5 允许的 notify_on 类目
VALID_NOTIFY_ON: frozenset[str] = frozenset({"need_user", "risk_red", "satisfied", "missed"})


@dataclass(frozen=True, slots=True)
class QuietHours:
    """安静时间（SPEC §6.1 attention.quiet_hours）。"""

    start: str  # HH:MM
    end: str  # HH:MM
    timezone: str  # IANA tz id；本期不解析，仅透传

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self._is_hhmm(self.start):
            errors.append(f"attention.quiet_hours.start must be HH:MM, got {self.start!r}")
        if not self._is_hhmm(self.end):
            errors.append(f"attention.quiet_hours.end must be HH:MM, got {self.end!r}")
        if not self.timezone.strip():
            errors.append("attention.quiet_hours.timezone must not be empty")
        return errors

    @staticmethod
    def _is_hhmm(s: str) -> bool:
        if len(s) != 5 or s[2] != ":":
            return False
        try:
            hh, mm = int(s[:2]), int(s[3:])
            return 0 <= hh < 24 and 0 <= mm < 60
        except ValueError:
            return False


@dataclass(frozen=True, slots=True)
class Attention:
    """通知偏好（SPEC §6.1 attention）。"""

    notify_on: tuple[str, ...] = field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    bypass_quiet_hours_on: tuple[str, ...] = field(default_factory=tuple)

    def validate(self) -> list[str]:
        errors: list[str] = []
        bad = set(self.notify_on) - VALID_NOTIFY_ON
        if bad:
            errors.append(f"attention.notify_on has unknown entries: {sorted(bad)}")
        bad_bypass = set(self.bypass_quiet_hours_on) - VALID_NOTIFY_ON
        if bad_bypass:
            errors.append(
                f"attention.bypass_quiet_hours_on has unknown entries: {sorted(bad_bypass)}"
            )
        if self.quiet_hours is not None:
            for err in self.quiet_hours.validate():
                errors.append(f"attention.quiet_hours.{err}")
        return errors


def to_dict(attention: Attention) -> dict[str, Any]:
    return {
        "notify_on": list(attention.notify_on),
        "quiet_hours": (
            {
                "start": attention.quiet_hours.start,
                "end": attention.quiet_hours.end,
                "timezone": attention.quiet_hours.timezone,
            }
            if attention.quiet_hours is not None
            else None
        ),
        "bypass_quiet_hours_on": list(attention.bypass_quiet_hours_on),
    }


def from_dict(data: dict[str, Any] | None) -> Attention:
    if not isinstance(data, dict) or not data:
        return Attention()
    qh_raw = data.get("quiet_hours")
    quiet_hours = (
        QuietHours(
            start=str(qh_raw["start"]),
            end=str(qh_raw["end"]),
            timezone=str(qh_raw["timezone"]),
        )
        if isinstance(qh_raw, dict)
        else None
    )
    return Attention(
        notify_on=tuple(str(x) for x in data.get("notify_on") or ()),
        quiet_hours=quiet_hours,
        bypass_quiet_hours_on=tuple(str(x) for x in data.get("bypass_quiet_hours_on") or ()),
    )
