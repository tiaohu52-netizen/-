"""确定性 acceptance check 执行器。

只执行合同声明的结构化检查；所有路径限制在 workspace 内，命令使用
argv + shell=False。无法确定的检查返回 ``undetermined``，不伪造通过。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from longtask.acceptance.checks import CheckKind, CheckSpec


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    outcome: str
    source: str
    details: str = ""

    def to_evidence(self) -> dict[str, str]:
        return {
            "check_id": self.check_id,
            "outcome": self.outcome,
            "source": self.source,
            "details": self.details,
        }


def _safe_target(root: Path, target: str) -> Path | None:
    candidate = (root / target).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def evaluate_check(spec: CheckSpec, *, workspace_root: Path) -> CheckResult:
    """执行单条 check，异常一律转为可审计的失败/不确定结果。"""
    check_id = f"{spec.kind.value}:{spec.target}"
    target = _safe_target(workspace_root, spec.target)
    if spec.kind in (
        CheckKind.FILE_EXISTS,
        CheckKind.FILE_CONTENT_MATCHES,
        CheckKind.ARTIFACT_PRESENT,
        CheckKind.STRUCTURE_VALID,
    ):
        if target is None:
            return CheckResult(check_id, "fail", "path-policy", "target escapes workspace")
        if not target.is_file():
            return CheckResult(check_id, "fail", str(target), "artifact does not exist")

    try:
        if spec.kind in (CheckKind.FILE_EXISTS, CheckKind.ARTIFACT_PRESENT):
            return CheckResult(check_id, "pass", str(target or spec.target))
        if spec.kind == CheckKind.FILE_CONTENT_MATCHES:
            text = target.read_text(encoding="utf-8")  # type: ignore[union-attr]
            if "sha256" in spec.args:
                digest = hashlib.sha256(target.read_bytes()).hexdigest()  # type: ignore[union-attr]
                return CheckResult(
                    check_id,
                    "pass" if digest == spec.args["sha256"] else "fail",
                    str(target),
                    digest,
                )
            expected = spec.args.get("contains")
            pattern = spec.args.get("regex")
            if expected is None and pattern is None:
                return CheckResult(check_id, "undetermined", str(target), "missing contains/regex")
            matched = (
                str(expected) in text
                if expected is not None
                else re.search(str(pattern), text) is not None
            )
            return CheckResult(check_id, "pass" if matched else "fail", str(target))
        if spec.kind == CheckKind.COMMAND_EXIT_ZERO:
            argv = [spec.target, *(str(x) for x in spec.args.get("argv", ()))]
            completed = subprocess.run(  # noqa: S603 — structured argv + shell=False
                argv,
                cwd=workspace_root,
                shell=False,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            return CheckResult(
                check_id,
                "pass" if completed.returncode == 0 else "fail",
                " ".join(argv),
                f"exit={completed.returncode}",
            )
        if spec.kind == CheckKind.STRUCTURE_VALID:
            json.loads(target.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            return CheckResult(check_id, "pass", str(target))
        return CheckResult(
            check_id,
            "undetermined",
            spec.target,
            "requires verifier or human observation",
        )
    except (OSError, json.JSONDecodeError, re.error, subprocess.SubprocessError) as exc:
        return CheckResult(check_id, "undetermined", spec.target, str(exc))


__all__ = ["CheckResult", "evaluate_check"]
