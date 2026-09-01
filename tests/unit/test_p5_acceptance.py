"""P5 acceptance checks 单元测试。"""

from __future__ import annotations

import pytest

from longtask.acceptance.checks import (
    CheckKind,
    CheckSpec,
    RepairBrief,
)

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
