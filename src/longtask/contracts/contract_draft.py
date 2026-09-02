"""ContractDraft 顶层容器（SPEC §4、§6.1）。

P2 起独立模块。仅组装 acceptance / authority / attention / continuity / budget
五个子 dataclass；不再内联它们的字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from longtask.contracts.acceptance import Acceptance
from longtask.contracts.attention import Attention
from longtask.contracts.attention import from_dict as attention_from_dict
from longtask.contracts.authority import Authority
from longtask.contracts.authority import from_dict as authority_from_dict
from longtask.contracts.budget import Budget
from longtask.contracts.continuity import Continuity
from longtask.contracts.continuity import from_dict as continuity_from_dict

SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class ContractDraft:
    """合同草案（SPEC §4 + §6.1）。

    字段切分：核心三件（title/objective/deadline_at）+ 冻结 hard_constraints +
    acceptance/authority/attention/continuity/budget 五个子组。soft_guidance /
    context / execution / client_meta 留作软组。
    """

    title: str
    objective: str
    deadline_at: datetime
    hard_constraints: dict[str, Any]
    acceptance: Acceptance
    workload_initial_hours: float
    budget: Budget
    authority: Authority = field(default_factory=Authority)
    attention: Attention = field(default_factory=Attention)
    continuity: Continuity = field(default_factory=Continuity)
    soft_guidance: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    client_meta: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not (0 < len(self.title) <= 200):
            errors.append("title must be 1..200 chars")
        if not self.objective.strip():
            errors.append("objective must not be empty")
        if self.deadline_at.tzinfo is None:
            errors.append("deadline_at must carry an explicit timezone")
        if self.workload_initial_hours <= 0:
            errors.append("workload_estimate.initial_hours must be positive")
        errors.extend(self.acceptance.validate())
        errors.extend(self.budget.validate())
        errors.extend(self.authority.validate())
        errors.extend(self.attention.validate())
        errors.extend(self.continuity.validate())
        return errors

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（SPEC §4、§6.1、§11.6）。"""
        from longtask.contracts.attention import to_dict as attention_to_dict
        from longtask.contracts.authority import to_dict as authority_to_dict
        from longtask.contracts.continuity import to_dict as continuity_to_dict

        return {
            "schema_version": SCHEMA_VERSION,
            "title": self.title,
            "objective": self.objective,
            "deadline_at": self.deadline_at.isoformat(),
            "hard_constraints": self.hard_constraints,
            "acceptance": {
                "standard": self.acceptance.standard,
                "checks": list(self.acceptance.checks),
                "verifier": self.acceptance.verifier,
            },
            "workload_estimate": {
                "initial_hours": self.workload_initial_hours,
            },
            "workload_initial_hours": self.workload_initial_hours,
            "budget": {
                "max_dispatches": self.budget.max_dispatches,
                "max_escalations": self.budget.max_escalations,
                "max_concurrent_attempts": self.budget.max_concurrent_attempts,
                "max_attempt_minutes": self.budget.max_attempt_minutes,
                "max_output_bytes": self.budget.max_output_bytes,
                "verification_attempts_reserved": self.budget.verification_attempts_reserved,
            },
            "authority": authority_to_dict(self.authority),
            "attention": attention_to_dict(self.attention),
            "continuity": continuity_to_dict(self.continuity),
            "soft_guidance": self.soft_guidance,
            "context": self.context,
            "execution": self.execution,
            "client_meta": self.client_meta,
        }


def from_dict(data: dict[str, Any]) -> ContractDraft:
    """从字典反序列化为 ContractDraft（与 to_dict 严格对称）。"""
    title = str(data["title"])
    objective = str(data["objective"])
    raw_deadline = data["deadline_at"]
    deadline_at = (
        raw_deadline
        if isinstance(raw_deadline, datetime)
        else datetime.fromisoformat(str(raw_deadline))
    )

    acceptance_raw = data["acceptance"]
    acceptance = Acceptance(
        standard=str(acceptance_raw["standard"]),
        checks=tuple(str(c) for c in acceptance_raw.get("checks") or ()),
        verifier=str(acceptance_raw.get("verifier") or "cross_check"),
    )

    workload_raw = data.get("workload_estimate") or {}
    if "initial_hours" in workload_raw:
        workload_initial_hours = float(workload_raw["initial_hours"])
    elif "workload_initial_hours" in data:
        workload_initial_hours = float(data["workload_initial_hours"])
    else:
        raise KeyError("workload_estimate.initial_hours")

    budget_raw = data["budget"]
    budget = Budget(
        max_dispatches=int(budget_raw["max_dispatches"]),
        max_escalations=int(budget_raw["max_escalations"]),
        max_concurrent_attempts=int(budget_raw["max_concurrent_attempts"]),
        max_attempt_minutes=int(budget_raw["max_attempt_minutes"]),
        max_output_bytes=int(budget_raw["max_output_bytes"]),
        verification_attempts_reserved=int(budget_raw.get("verification_attempts_reserved", 1)),
    )

    return ContractDraft(
        title=title,
        objective=objective,
        deadline_at=deadline_at,
        hard_constraints=dict(data.get("hard_constraints") or {}),
        acceptance=acceptance,
        workload_initial_hours=workload_initial_hours,
        budget=budget,
        authority=authority_from_dict(data.get("authority")),
        attention=attention_from_dict(data.get("attention")),
        continuity=continuity_from_dict(data.get("continuity")),
        soft_guidance=dict(data.get("soft_guidance") or {}),
        context=dict(data.get("context") or {}),
        execution=dict(data.get("execution") or {}),
        client_meta=dict(data.get("client_meta") or {}),
    )
