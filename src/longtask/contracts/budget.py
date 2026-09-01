"""Budget 字段（SPEC §4、§6.2）。

P2 起独立模块；约束校验集中在本文件，避免在 ContractDraft 与 CLI 之间漂移。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Budget:
    """开销上限（SPEC §4 budget）。

    推爆就地转 blocked；五项均须为正数（P2：与单一 validator 对齐）。
    P5 验证预算（verification_attempts_reserved）由 contracts/budget.py 后续扩展，
    本期不引入以避免虚假引用。
    """

    max_dispatches: int
    max_escalations: int
    max_concurrent_attempts: int
    max_attempt_minutes: int
    max_output_bytes: int

    def validate(self) -> list[str]:
        """返回违规项列表；空列表表示合法。"""
        errors: list[str] = []
        for name, value in (
            ("max_dispatches", self.max_dispatches),
            ("max_escalations", self.max_escalations),
            ("max_concurrent_attempts", self.max_concurrent_attempts),
            ("max_attempt_minutes", self.max_attempt_minutes),
            ("max_output_bytes", self.max_output_bytes),
        ):
            if value <= 0:
                errors.append(f"budget.{name} must be positive, got {value}")
        return errors
