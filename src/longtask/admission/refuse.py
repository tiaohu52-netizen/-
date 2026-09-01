"""Admission refuse（SPEC §10.4）。

`goal/prepare` MUST 默认拒接不合格者；本模块承载 AdmissionRefused 与拒接
原因码（区别于 RPC 错误码：rpc/errors.py 是 RPC 层，admission/refuse.py 是
业务语义层）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AdmissionRefuseCode(StrEnum):
    """SPEC §10.4 拒接原因码（业务语义）。"""

    NO_ELIGIBLE_EXECUTOR = "no-eligible-executor"  # §6.3 候选全失败
    ACCEPTANCE_NOT_EXECUTABLE = "acceptance-not-executable"  # 验收无 checker / 缺独立候选
    VERIFICATION_RESERVE_INSUFFICIENT = "verification-reserve-insufficient"  # P5 完整规则
    FORECAST_P90_EXCEEDS_DEADLINE = "forecast-p90-exceeds-deadline"  # P4 完整规则
    BUDGET_BELOW_ONE_VERIFICATION = "budget-below-one-verification"
    POLICY_DENY = "policy-deny"  # authority.executor_policy=closed 且无显式 allow


@dataclass(frozen=True, slots=True)
class AdmissionRefusedError(Exception):
    """goal/prepare 默认拒接（SPEC §10.4）。"""

    code: AdmissionRefuseCode
    reason: str
    detail: dict[str, str] | None = None

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return f"{self.code.value}: {self.reason}"
