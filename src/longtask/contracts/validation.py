"""单一 runtime validator（SPEC §22 + CONTRIBUTING「模块边界约束」）。

P2 起，contract draft 的校验走本文件的 `validate_draft()` 单一入口。JSON
Schema（schemas/contract.schema.json）保留为设计参考文档，不再参与 runtime
校验——避免 JSON Schema / dataclass 默认值 / CLI 默认值三处漂移。

使用方式：

    from longtask.contracts.validation import validate_draft

    errors = validate_draft(draft_dict)
    if errors:
        raise ValueError("; ".join(errors))
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from longtask.contracts.acceptance import VALID_VERIFIER_KINDS
from longtask.contracts.contract_draft import ContractDraft, from_dict

__all__ = ["validate_draft", "validate_raw"]


def validate_raw(data: object) -> list[str]:
    """在 to ContractDraft 之前对原始 dict 做结构与字段校验。

    返回 errors 列表；空表示通过。本函数不依赖 dataclass 类型，确保 CLI /
    JSON Schema / dataclass 三条路径在落库前都汇聚到同一组错误。
    """
    if not isinstance(data, dict):
        return ["draft must be a dict"]
    return _validate_raw_dict(data)


def _validate_raw_dict(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    title = data.get("title")
    if not isinstance(title, str) or not (0 < len(title) <= 200):
        errors.append(f"title must be 1..200 chars, got {type(title).__name__}")

    objective = data.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        errors.append("objective must be a non-empty string")

    raw_deadline = data.get("deadline_at")
    if not _looks_like_datetime_with_tz(raw_deadline):
        errors.append(
            f"deadline_at must be ISO-8601 with timezone, got {type(raw_deadline).__name__}"
        )

    hard = data.get("hard_constraints")
    if not isinstance(hard, dict):
        errors.append("hard_constraints must be a dict")

    acc = data.get("acceptance")
    if not isinstance(acc, dict):
        errors.append("acceptance must be a dict")
    else:
        std = acc.get("standard")
        if not isinstance(std, str) or not std.strip():
            errors.append("acceptance.standard must be a non-empty string")
        checks = acc.get("checks")
        if not isinstance(checks, (list, tuple)) or not checks:
            errors.append("acceptance.checks must be a non-empty list")
        verifier = acc.get("verifier")
        if verifier not in VALID_VERIFIER_KINDS:
            errors.append(
                f"acceptance.verifier must be one of {sorted(VALID_VERIFIER_KINDS)}, "
                f"got {verifier!r}"
            )

    workload = data.get("workload_estimate") or {}
    init_hours = workload.get("initial_hours") if isinstance(workload, dict) else None
    if init_hours is None:
        init_hours = data.get("workload_initial_hours")
    if not isinstance(init_hours, (int, float)) or init_hours <= 0:
        errors.append("workload_estimate.initial_hours must be a positive number")

    budget = data.get("budget")
    if not isinstance(budget, dict):
        errors.append("budget must be a dict")
    else:
        for name in (
            "max_dispatches",
            "max_escalations",
            "max_concurrent_attempts",
            "max_attempt_minutes",
            "max_output_bytes",
        ):
            v = budget.get(name)
            if not isinstance(v, int) or v <= 0:
                errors.append(f"budget.{name} must be a positive int, got {v!r}")

    return errors


def validate_draft(draft: ContractDraft | dict[str, Any]) -> list[str]:
    """单一 runtime validator 入口（SPEC §22）。

    接受 dict（CLI/MCP/JSON 入口）或 ContractDraft（dataclass 内部入口）；
    两条路径都收敛到 dataclass.validate()。
    """
    if isinstance(draft, dict):
        errs = validate_raw(draft)
        if errs:
            return errs
        try:
            draft = from_dict(draft)
        except (KeyError, TypeError, ValueError) as exc:
            return [f"draft.to_dataclass failed: {exc}"]
    return draft.validate()


def _looks_like_datetime_with_tz(value: Any) -> bool:
    if isinstance(value, datetime):
        return value.tzinfo is not None
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None
