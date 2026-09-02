"""7 种 typed checks（SPEC §12.1）。

骨架期仅承载类型枚举与 dataclass；具体 check_kind 字段定义在
schemas/contract.schema.json 中间对齐。本模块不执行 check——只承载
数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CheckKind(StrEnum):
    """验收 check 类型（SPEC §12.1 共 7 种）。"""

    FILE_EXISTS = "file-exists"  # workspace 内某路径存在
    FILE_CONTENT_MATCHES = "file-content-matches"  # 文件内容匹配某 pattern/regex
    COMMAND_EXIT_ZERO = "command-exit-zero"  # 跑某命令并断言 exit code
    ARTIFACT_PRESENT = "artifact-present"  # 工作区产出某命名 artifact
    STRUCTURE_VALID = "structure-valid"  # 某 JSON/YAML 结构合法
    OBSERVABLE = "observable"  # 端到端可观察（per evidence kind 推断）
    USER_ASSERTION = "user-assertion"  # 留待用户/模型侧事后补强


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """单条 check 的声明（与 acceptance.checks 数组一一对应）。"""

    kind: CheckKind
    target: str  # 文件路径 / 命令 / 结构 schema 路径 / 观察路径
    args: dict[str, Any] = field(default_factory=dict)
    mandatory: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "target": self.target,
            "args": dict(self.args),
            "mandatory": self.mandatory,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckSpec:
        """从合同 JSON 读取 typed check，并在边界处拒绝畸形值。"""
        if not isinstance(data, dict):
            raise TypeError("check must be an object")
        return cls(
            kind=CheckKind(str(data["kind"])),
            target=str(data["target"]),
            args=dict(data.get("args") or {}),
            mandatory=bool(data.get("mandatory", True)),
            note=str(data.get("note", "")),
        )


def parse_check(value: str | dict[str, Any]) -> str | CheckSpec:
    """兼容旧自然语言 check，同时保留新的 typed check 对象。"""
    if isinstance(value, dict):
        return CheckSpec.from_dict(value)
    return str(value)


@dataclass(frozen=True, slots=True)
class RepairBrief:
    """verifier 失败时结构化输出（SPEC §12.4 修复闭环）。

    取代原 'reason: str' 自由文本：含失败 check 列表、最小可重跑上下文、
    retry 建议、是否触发 user escalation。
    """

    failed_checks: tuple[str, ...] = ()
    context_pointer: str = ""  # workspace 内某路径作为下次 attempt 上下文
    retry_strategy: str = "respawn"  # respawn | swap_executor | escalate_user | abandon
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "failed_checks": list(self.failed_checks),
            "context_pointer": self.context_pointer,
            "retry_strategy": self.retry_strategy,
            "notes": list(self.notes),
        }


__all__ = ["CheckKind", "CheckSpec", "RepairBrief", "parse_check"]
