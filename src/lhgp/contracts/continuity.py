"""Continuity configuration (SPEC §6.1 and §11), owned by ``lhgp``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Continuity:
    """Checkpoint, recovery grace, and capsule capacity policy."""

    checkpoint_max_age_minutes: int = 20
    checkpoint_on_material_change: bool = True
    recovery_grace_minutes: int = 5
    capsule_max_tokens: int = 12000

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.checkpoint_max_age_minutes <= 0:
            errors.append(
                f"continuity.checkpoint_max_age_minutes must be positive, "
                f"got {self.checkpoint_max_age_minutes}"
            )
        if self.recovery_grace_minutes < 0:
            errors.append(
                f"continuity.recovery_grace_minutes must be non-negative, "
                f"got {self.recovery_grace_minutes}"
            )
        if self.capsule_max_tokens <= 0:
            errors.append(
                f"continuity.capsule_max_tokens must be positive, got {self.capsule_max_tokens}"
            )
        return errors


def to_dict(continuity: Continuity) -> dict[str, Any]:
    return {
        "checkpoint_max_age_minutes": continuity.checkpoint_max_age_minutes,
        "checkpoint_on_material_change": continuity.checkpoint_on_material_change,
        "recovery_grace_minutes": continuity.recovery_grace_minutes,
        "capsule_max_tokens": continuity.capsule_max_tokens,
    }


def from_dict(data: dict[str, Any] | None) -> Continuity:
    if not isinstance(data, dict) or not data:
        return Continuity()
    return Continuity(
        checkpoint_max_age_minutes=int(data.get("checkpoint_max_age_minutes") or 20),
        checkpoint_on_material_change=bool(data.get("checkpoint_on_material_change", True)),
        recovery_grace_minutes=int(data.get("recovery_grace_minutes") or 5),
        capsule_max_tokens=int(data.get("capsule_max_tokens") or 12000),
    )


__all__ = ["Continuity", "from_dict", "to_dict"]
