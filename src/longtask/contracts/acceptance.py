"""Acceptance 字段（SPEC §4 acceptance + §5.2 verifier 默认）。

P2 起独立模块。本期仅承载字段与最小校验；7 种 typed checks（§12.1）的具体
evidence 实体结构留到 P5 引入——避免本阶段写虚假引用。
"""

from __future__ import annotations

from dataclasses import dataclass

# 唯一权威 verifier 集合（与 contracts/validation.py 共享）。
VALID_VERIFIER_KINDS: frozenset[str] = frozenset({"cross_check", "none"})


@dataclass(frozen=True, slots=True)
class Acceptance:
    """验收条款（SPEC §4 acceptance + §5.2 verifier 默认）。

    本期仅承载 standard / checks / verifier 三字段；typed check 类型与
    evidence 实体留待 P5（typed-acceptance-claim）。
    """

    standard: str
    checks: tuple[str, ...]
    verifier: str = "cross_check"  # cross_check | none

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.standard.strip():
            errors.append("acceptance.standard must not be empty")
        if not self.checks:
            errors.append("acceptance.checks must have at least one item")
        if self.verifier not in VALID_VERIFIER_KINDS:
            errors.append(f"acceptance.verifier unknown: {self.verifier}")
        return errors
