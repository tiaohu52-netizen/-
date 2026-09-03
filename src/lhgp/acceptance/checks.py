"""Typed acceptance checks (SPEC §12.1).

The canonical ``lhgp`` namespace owns these value types.  The historical
``longtask.acceptance.checks`` path remains a compatibility facade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CheckKind(StrEnum):
    """Acceptance check type (the seven kinds defined by SPEC §12.1)."""

    FILE_EXISTS = "file-exists"
    FILE_CONTENT_MATCHES = "file-content-matches"
    COMMAND_EXIT_ZERO = "command-exit-zero"
    ARTIFACT_PRESENT = "artifact-present"
    STRUCTURE_VALID = "structure-valid"
    OBSERVABLE = "observable"
    USER_ASSERTION = "user-assertion"


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """One independently verifiable acceptance check declaration."""

    kind: CheckKind
    target: str
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
    """Accept legacy natural-language checks alongside typed checks."""
    if isinstance(value, dict):
        return CheckSpec.from_dict(value)
    return str(value)


def check_identity(check: str | CheckSpec) -> str:
    """Canonical comparable identity for one acceptance check.

    A Goal stage declares its required acceptance as plain references while a
    contract carries either typed objects or legacy free text, so the two sides
    are not directly comparable — a typed ``CheckSpec`` is not even hashable,
    because ``args`` is a mutable mapping.  Collapsing both sides to one
    identity string is what makes "the contract covers the stage" decidable.

    Typed checks reduce to ``<kind>:<target>``: ``args`` deliberately does not
    participate, so a stage requirement stays satisfiable by any contract check
    aimed at the same kind and target regardless of its argument details.
    Legacy free-text checks compare as their own trimmed text.
    """
    if isinstance(check, CheckSpec):
        return f"{check.kind.value}:{check.target}"
    return str(check).strip()


@dataclass(frozen=True, slots=True)
class RepairBrief:
    """Structured verifier failure output for the repair loop."""

    failed_checks: tuple[str, ...] = ()
    context_pointer: str = ""
    retry_strategy: str = "respawn"
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "failed_checks": list(self.failed_checks),
            "context_pointer": self.context_pointer,
            "retry_strategy": self.retry_strategy,
            "notes": list(self.notes),
        }


__all__ = ["CheckKind", "CheckSpec", "RepairBrief", "check_identity", "parse_check"]
