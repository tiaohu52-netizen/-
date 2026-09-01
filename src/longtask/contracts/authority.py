"""Authority 字段（SPEC §6.1 authority + §6.3 授权语义 7 条件）。

P2 起独立模块。实现 §6.3 的 7 个候选条件 + global executor_policy 约束；
不影响 admission / registry 的实现位置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# SPEC §6.3 允许的 controls 闭集（按 §6.1 示例 + §10.4 通知语义）
ALLOWED_CONTROLS: frozenset[str] = frozenset({"notify", "followup", "steer", "spawn"})


@dataclass(frozen=True, slots=True)
class AuthorityBinding:
    """一次具体绑定：哪个 executor + 哪几个 model + 哪几个 role（SPEC §6.1）。"""

    executor_id: str
    models: tuple[str, ...]  # 含 "*" 表示模型通配（仅 Principal 明确允许时合法）
    roles: tuple[str, ...]  # executor / verifier / planner ...

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.executor_id.strip():
            errors.append("authority.executors[].executor_id must not be empty")
        if not self.models:
            errors.append("authority.executors[].models must not be empty")
        if not self.roles:
            errors.append("authority.executors[].roles must not be empty")
        return errors


@dataclass(frozen=True, slots=True)
class Authority:
    """合同授权（SPEC §6.1 authority）。

    executor_policy: explicit_allow | closed（仅显式 allow 列表里的候选过）。
    required_capabilities: 该合同必须由具备的能力集合（candidate.capabilities ⊇ 此集合）。
    allowed_controls: 该合同允许的 control 类（notify/followup/steer/spawn）。
    allow_parallel: 是否允许并行 attempt；仅单一合同可承担并行任务时为 true。

    P2：实现 §6.3 的 7 条件判定（satisfied_by_candidate）；P5 之前 §6.3 中
    "verifier_independence_satisfies" 留接口位、由 attempt_runner 在派 verifier
    时校验（acceptance.independence 已在 dataclass 中预留字段，本期未启用）。
    """

    executor_policy: str = "closed"  # closed | explicit_allow（默认 closed）
    executors: tuple[AuthorityBinding, ...] = field(default_factory=tuple)
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    allowed_controls: tuple[str, ...] = field(default_factory=tuple)
    allow_parallel: bool = False

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.executor_policy not in ("closed", "explicit_allow"):
            errors.append(
                f"authority.executor_policy must be 'closed' or 'explicit_allow', "
                f"got {self.executor_policy!r}"
            )
        for idx, binding in enumerate(self.executors):
            for err in binding.validate():
                errors.append(f"authority.executors[{idx}].{err}")
        unknown = set(self.allowed_controls) - ALLOWED_CONTROLS
        if unknown:
            errors.append(f"authority.allowed_controls has unknown entries: {sorted(unknown)}")
        return errors


def models_allow(authority: Authority, *, binding: AuthorityBinding, model: str) -> bool:
    """binding.models 是否覆盖 model（SPEC §6.1 "[\"*\"] 仅 Principal 明确选择时合法"）。"""
    if not binding.models:
        return False
    if "*" in binding.models:
        return True  # 显式通配；与 enabled 共同校验由 caller 负责
    return model in binding.models


def roles_allow(authority: Authority, *, binding: AuthorityBinding, role: str) -> bool:
    return role in binding.roles


def binding_for_executor(authority: Authority, executor_id: str) -> AuthorityBinding | None:
    """查 binding（SPEC §6.1 executors 数组按 executor_id 索引）。"""
    for b in authority.executors:
        if b.executor_id == executor_id:
            return b
    return None


def to_dict(authority: Authority) -> dict[str, Any]:
    """序列化为线协议字典格式（SPEC §6.1 authority 段）。"""
    return {
        "executor_policy": authority.executor_policy,
        "executors": [
            {
                "executor_id": b.executor_id,
                "models": list(b.models),
                "roles": list(b.roles),
            }
            for b in authority.executors
        ],
        "required_capabilities": list(authority.required_capabilities),
        "allowed_controls": list(authority.allowed_controls),
        "allow_parallel": authority.allow_parallel,
    }


def from_dict(data: dict[str, Any] | None) -> Authority:
    """反序列化为 Authority（默认 closed + 空 executors）。"""
    if not isinstance(data, dict) or not data:
        return Authority()
    executors_raw = data.get("executors") or []
    executors = tuple(
        AuthorityBinding(
            executor_id=str(b["executor_id"]),
            models=tuple(str(m) for m in b.get("models") or ()),
            roles=tuple(str(r) for r in b.get("roles") or ()),
        )
        for b in executors_raw
        if isinstance(b, dict)
    )
    return Authority(
        executor_policy=str(data.get("executor_policy") or "closed"),
        executors=executors,
        required_capabilities=tuple(str(c) for c in data.get("required_capabilities") or ()),
        allowed_controls=tuple(str(c) for c in data.get("allowed_controls") or ()),
        allow_parallel=bool(data.get("allow_parallel") or False),
    )
