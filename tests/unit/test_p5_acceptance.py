"""P5 acceptance checks 单元测试。"""

from __future__ import annotations

import pytest

from longtask.acceptance.checks import (
    CheckKind,
    CheckSpec,
    RepairBrief,
)
from longtask.acceptance.evaluator import evaluate_check

pytestmark = pytest.mark.unit


class TestCheckKind:
    def test_seven_kinds(self) -> None:
        assert len(list(CheckKind)) == 7

    def test_values_lower_kebab(self) -> None:
        for k in CheckKind:
            assert k.value == k.value.lower()
            assert " " not in k.value


class TestCheckSpec:
    def test_to_dict_mandatory_default_true(self) -> None:
        s = CheckSpec(kind=CheckKind.FILE_EXISTS, target="result.txt")
        d = s.to_dict()
        assert d["kind"] == "file-exists"
        assert d["target"] == "result.txt"
        assert d["mandatory"] is True
        assert d["args"] == {}

    def test_to_dict_optional(self) -> None:
        s = CheckSpec(
            kind=CheckKind.COMMAND_EXIT_ZERO,
            target="pytest",
            args={"args": ["-q"]},
            mandatory=False,
        )
        d = s.to_dict()
        assert d["mandatory"] is False
        assert d["args"] == {"args": ["-q"]}


class TestRepairBrief:
    def test_default_empty(self) -> None:
        b = RepairBrief()
        d = b.to_dict()
        assert d == {
            "failed_checks": [],
            "context_pointer": "",
            "retry_strategy": "respawn",
            "notes": [],
        }

    def test_with_payload(self) -> None:
        b = RepairBrief(
            failed_checks=("file-exists:result.txt",),
            context_pointer=".workbuddy/handover/lt-x.md",
            retry_strategy="swap_executor",
            notes=("尝试过 exec-a, exec-b 均失败",),
        )
        d = b.to_dict()
        assert d["failed_checks"] == ["file-exists:result.txt"]
        assert d["retry_strategy"] == "swap_executor"
        assert d["notes"] == ["尝试过 exec-a, exec-b 均失败"]


class TestEvaluateCheck:
    def test_file_exists_and_hash_are_deterministic(self, tmp_path) -> None:
        target = tmp_path / "result.txt"
        target.write_text("hello", encoding="utf-8")
        exists = evaluate_check(
            CheckSpec(kind=CheckKind.FILE_EXISTS, target="result.txt"), workspace_root=tmp_path
        )
        hashed = evaluate_check(
            CheckSpec(
                kind=CheckKind.FILE_CONTENT_MATCHES,
                target="result.txt",
                args={"contains": "ell"},
            ),
            workspace_root=tmp_path,
        )
        assert exists.outcome == "pass"
        assert hashed.outcome == "pass"

    def test_path_escape_fails_closed(self, tmp_path) -> None:
        result = evaluate_check(
            CheckSpec(kind=CheckKind.FILE_EXISTS, target="../outside"), workspace_root=tmp_path
        )
        assert result.outcome == "fail"

    def test_command_uses_structured_argv(self, tmp_path) -> None:
        result = evaluate_check(
            CheckSpec(
                kind=CheckKind.COMMAND_EXIT_ZERO,
                target="python",
                args={"argv": ["-c", "raise SystemExit(0)"]},
            ),
            workspace_root=tmp_path,
        )
        assert result.outcome == "pass"
