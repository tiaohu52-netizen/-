"""Budget value object (SPEC §4, §6.2), owned by the canonical namespace."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_VERIFICATION_RESERVED = 2


@dataclass(frozen=True, slots=True)
class Budget:
    """Execution, escalation, output, and verification limits."""

    max_dispatches: int
    max_escalations: int
    max_concurrent_attempts: int
    max_attempt_minutes: int
    max_output_bytes: int
    verification_attempts_reserved: int = DEFAULT_VERIFICATION_RESERVED

    def validate(self) -> list[str]:
        """Return violations; an empty list means the budget is valid."""
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


__all__ = ["DEFAULT_VERIFICATION_RESERVED", "Budget"]
