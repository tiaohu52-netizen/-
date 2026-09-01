"""P3 外部运行句柄（SPEC §11.3）单元测试。

关注 parse_legacy_session_ref：老 attempt 写库的 session_ref 仍是
subprocess:<attempt_id>:<pid> 字符串；P3 reconcile 需要把它解成
ExternalRunHandle 走四分支判定。
"""

from __future__ import annotations

import pytest

from longtask.adapters.handles import ExternalRunHandle, parse_legacy_session_ref

pytestmark = pytest.mark.unit


class TestParseLegacySessionRef:
    def test_subprocess_format_unpacks_pid_as_external_run_id(self) -> None:
        h = parse_legacy_session_ref("subprocess:att-20260901-001:12345")
        assert h.external_run_id == "12345"
        assert h.session_locator == "att-20260901-001"
        assert h.recovery_strategy == "orphan_grace"
        assert h.capability_snapshot == {"transport": "subprocess"}

    def test_unknown_format_defaults_to_fence_respawn(self) -> None:
        h = parse_legacy_session_ref("mcp-xyz")
        assert h.recovery_strategy == "fence_respawn"
        assert h.session_locator == "mcp-xyz"

    def test_to_dict_round_trip(self) -> None:
        h = ExternalRunHandle(
            external_run_id="abc",
            session_locator="sess-1",
            recovery_strategy="reattach",
            capability_snapshot={"x": 1},
        )
        d = h.to_dict()
        h2 = ExternalRunHandle.from_dict(d)
        assert h2 == h

    def test_from_dict_missing_keys_use_defaults(self) -> None:
        h = ExternalRunHandle.from_dict({})
        assert h.external_run_id == ""
        assert h.session_locator == ""
        assert h.recovery_strategy == "fence_respawn"
        assert h.capability_snapshot == {}
