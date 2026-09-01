"""§6.3 候选可用性 7 条件判定（SPEC §6.3）。

七个条件（来自 SPEC §6.3）：
1. globally_enabled
2. contract_explicitly_allows(executor, model, role)
3. capability_satisfies
4. constraint_enforcement_proven
5. budget_available
6. concurrency_available
7. verifier_independence_satisfies

本模块不实际执行"运行时报错"，只回答"这位候选在不在 §6.3 的 7 条件闭集
里"。每个条件对应一个布尔判定；全部为 True 才算 eligible。

调用方通常由 promoter / adapter_factory 在 prepare 路径使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from longtask.contracts.authority import (
    Authority,
    AuthorityBinding,
    binding_for_executor,
    models_allow,
    roles_allow,
)


@dataclass(frozen=True, slots=True)
class CandidateFacts:
    """候选执行器的事实地集（由 caller 注入）；不可信来源不写入本结构。"""

    executor_id: str
    executor_enabled_globally: bool  # 条件 1：全局开关
    executor_concurrency_available: bool  # 条件 6：并发余额
    capability_satisfied: bool  # 条件 3：authority.required_capabilities ⊇ candidate.capabilities
    constraint_enforcement_proven: bool  # 条件 4：adapter 能兑现 hard_constraints
    budget_available: bool  # 条件 5：max_dispatches > started_events
    verifier_independent: bool  # 条件 7：与执行者不同 attempt_id / session / 优选不同 model family


@dataclass(frozen=True, slots=True)
class EligibilityVerdict:
    """七条件联合判定结果（SPEC §6.3）。"""

    candidate_executor_id: str
    requested_model: str
    requested_role: str
    conditions: dict[str, bool] = field(default_factory=dict)

    @property
    def eligible(self) -> bool:
        return all(self.conditions.values())

    @property
    def failed(self) -> list[str]:
        return [name for name, ok in self.conditions.items() if not ok]


def evaluate(
    *,
    authority: Authority,
    facts: CandidateFacts,
    requested_model: str,
    requested_role: str,
) -> EligibilityVerdict:
    """判定一个候选是否满足 §6.3 全部 7 条件。

    不判定"模型白名单在哪 / 角色白名单在哪"——这些已注入 authority。
    """
    conditions: dict[str, bool] = {}

    # 条件 1：globally_enabled
    conditions["globally_enabled"] = facts.executor_enabled_globally

    # 条件 2：contract_explicitly_allows(executor, model, role)
    binding = binding_for_executor(authority, facts.executor_id)
    explicit_allowed = (
        binding is not None
        and models_allow(authority, binding=binding, model=requested_model)
        and roles_allow(authority, binding=binding, role=requested_role)
    )
    conditions["contract_explicitly_allows"] = explicit_allowed

    # 条件 3：capability_satisfies
    conditions["capability_satisfies"] = facts.capability_satisfied

    # 条件 4：constraint_enforcement_proven
    conditions["constraint_enforcement_proven"] = facts.constraint_enforcement_proven

    # 条件 5：budget_available
    conditions["budget_available"] = facts.budget_available

    # 条件 6：concurrency_available
    conditions["concurrency_available"] = facts.executor_concurrency_available

    # 条件 7：verifier_independence_satisfies
    # 仅当 requested_role == verifier 时需独立；若 role == executor 不强制
    if requested_role == "verifier":
        conditions["verifier_independence_satisfies"] = facts.verifier_independent
    else:
        # executor 不要求独立性；视为满足
        conditions["verifier_independence_satisfies"] = True

    return EligibilityVerdict(
        candidate_executor_id=facts.executor_id,
        requested_model=requested_model,
        requested_role=requested_role,
        conditions=conditions,
    )


__all__ = [
    "AuthorityBinding",
    "CandidateFacts",
    "EligibilityVerdict",
    "evaluate",
]
