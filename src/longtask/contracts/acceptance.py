"""Acceptance 字段（SPEC §4 acceptance + §5.2 verifier 默认）。

P2 起独立模块。本期仅承载字段与最小校验；7 种 typed checks（§12.1）的具体
evidence 实体结构留到 P5 引入——避免本阶段写虚假引用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from longtask.acceptance.checks import CheckSpec, parse_check

# 唯一权威 verifier 集合（与 contracts/validation.py 共享）。
VALID_VERIFIER_KINDS: frozenset[str] = frozenset({"cross_check", "none"})


@dataclass(frozen=True, slots=True)
class Acceptance:
    """验收条款（SPEC §4 acceptance + §5.2 verifier 默认）。

    本期仅承载 standard / checks / verifier 三字段；typed check 类型与
    evidence 实体留待 P5（typed-acceptance-claim）。
    """

    standard: str
    checks: tuple[str | CheckSpec, ...]
    verifier: str = "cross_check"  # cross_check | none

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.standard.strip():
            errors.append("acceptance.standard must not be empty")
        if not self.checks:
            errors.append("acceptance.checks must have at least one item")
        for index, check in enumerate(self.checks):
            if isinstance(check, CheckSpec):
                for error in _validate_check(check):
                    errors.append(f"acceptance.checks[{index}].{error}")
            elif not isinstance(check, str) or not check.strip():
                errors.append(f"acceptance.checks[{index}] must be a non-empty string or object")
        if self.verifier not in VALID_VERIFIER_KINDS:
            errors.append(f"acceptance.verifier unknown: {self.verifier}")
        return errors

    @classmethod
    def from_values(
        cls, standard: str, checks: tuple[str | dict[str, Any], ...], verifier: str
    ) -> Acceptance:
        return cls(
            standard=standard,
            checks=tuple(parse_check(item) for item in checks),
            verifier=verifier,
        )


def _validate_check(check: CheckSpec) -> list[str]:
    errors: list[str] = []
    if not check.target.strip():
        errors.append("target must not be empty")
    if check.kind.value == "command-exit-zero" and "argv" in check.args:
        argv = check.args["argv"]
        if not isinstance(argv, list) or any(
            not isinstance(item, str) or not item for item in argv
        ):
            errors.append("args.argv must be a list of non-empty strings")
    return errors
