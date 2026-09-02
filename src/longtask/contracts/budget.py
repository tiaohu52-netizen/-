"""Budget 字段（SPEC §4、§6.2）。

P2 起独立模块；约束校验集中在本文件，避免在 ContractDraft 与 CLI 之间漂移。
"""

from __future__ import annotations

from dataclasses import dataclass

# P5：未声明时的验证预算兜底——至少留一次 reverify，否则 verifier fail
# 后没有任何再验机会，repair 成果无从确认。
DEFAULT_VERIFICATION_RESERVED = 1


@dataclass(frozen=True, slots=True)
class Budget:
    """开销上限（SPEC §4 budget）。

    推爆就地转 blocked；六项均须为正数（P2：与单一 validator 对齐）。
    P5：verification_attempts_reserved 是**独立记账**的验证预算——
    verifier 派发消耗它而不消耗 max_dispatches（否则一轮验证就能吃光
    执行预算，§12.4 repair 闭环直接饿死）。未声明时兜底 1（至少一次
    reverify 机会）。
    """

    max_dispatches: int
    max_escalations: int
    max_concurrent_attempts: int
    max_attempt_minutes: int
    max_output_bytes: int
    verification_attempts_reserved: int = DEFAULT_VERIFICATION_RESERVED

    def validate(self) -> list[str]:
        """返回违规项列表；空列表表示合法。"""
        errors: list[str] = []
        for name, value in (
            ("max_dispatches", self.max_dispatches),
            ("max_escalations", self.max_escalations),
            ("max_concurrent_attempts", self.max_concurrent_attempts),
            ("max_attempt_minutes", self.max_attempt_minutes),
            ("max_output_bytes", self.max_output_bytes),
            ("verification_attempts_reserved", self.verification_attempts_reserved),
        ):
            if value < 0 or (name != "verification_attempts_reserved" and value == 0):
                errors.append(f"budget.{name} must be positive, got {value}")
        return errors
