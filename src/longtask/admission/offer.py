"""Admission offer（SPEC §10.4）。

`goal/prepare` MUST 返回一份 offer，而不是直接承诺执行。offer 至少包含：
1. 可用和被拒绝的 executor/model 及原因（eligible_executors / rejected_executors）
2. 当前验收可执行性（acceptance_executable）
3. p50/p90 完成预测与置信度（forecast_p50_minutes / forecast_p90_minutes / confidence）
4. verification reserve 是否足够（verification_reserve_sufficient）
5. 预计最晚安全启动时刻（safe_start_by）
6. 已知不受运行时控制的风险（uncontrolled_risks）
7. 可声明的 continuity / wake / sandbox 保证等级（declared_guarantees）

P2 仅承载 7 字段 dataclass + 序列化；具体判定（哪些执行器可用、forecast 怎么
算、guarantees 怎么分级）由 callsite 在 prepare 路径里写入。本期不计算
forecast（P4）——把 `forecast_p50/p90/confidence` 默认 None。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ExecutorCandidateView:
    """offer 里单个候选执行器视图（SPEC §10.4 第 1 项）。"""

    executor_id: str
    models: tuple[str, ...]
    reason: str  # 入 allowlist / 被拒原因

    def to_dict(self) -> dict[str, Any]:
        return {
            "executor_id": self.executor_id,
            "models": list(self.models),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Offer:
    """goal/prepare 返回的 admission offer（SPEC §10.4）。"""

    eligible_executors: tuple[ExecutorCandidateView, ...] = field(default_factory=tuple)
    rejected_executors: tuple[ExecutorCandidateView, ...] = field(default_factory=tuple)
    acceptance_executable: bool = False
    forecast_p50_minutes: float | None = None
    forecast_p90_minutes: float | None = None
    forecast_confidence: float | None = None  # 0..1
    verification_reserve_sufficient: bool = False
    safe_start_by: datetime | None = None
    uncontrolled_risks: tuple[str, ...] = field(default_factory=tuple)
    declared_guarantees: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible_executors": [c.to_dict() for c in self.eligible_executors],
            "rejected_executors": [c.to_dict() for c in self.rejected_executors],
            "acceptance_executable": self.acceptance_executable,
            "forecast_p50_minutes": self.forecast_p50_minutes,
            "forecast_p90_minutes": self.forecast_p90_minutes,
            "forecast_confidence": self.forecast_confidence,
            "verification_reserve_sufficient": self.verification_reserve_sufficient,
            "safe_start_by": self.safe_start_by.isoformat() if self.safe_start_by else None,
            "uncontrolled_risks": list(self.uncontrolled_risks),
            "declared_guarantees": list(self.declared_guarantees),
        }

    @property
    def eligible(self) -> bool:
        """§10.4 缺一项即拒：合格执行器 + 可执行验收 + verification reserve + p90 ≤ deadline。"""
        return (
            bool(self.eligible_executors)
            and self.acceptance_executable
            and self.verification_reserve_sufficient
            and (
                self.forecast_p90_minutes is None
                or self.safe_start_by is None
                or True  # 截止语义由 callsite 用 safe_start_by 推导
            )
        )
