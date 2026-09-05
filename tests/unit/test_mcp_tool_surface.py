"""MCP 工具面一致性回归（工具面全量审计 C1/C2 + goal/update 门禁）。

- C1：字符串 acceptance_checks 曾被逐字符拆成垃圾检查冻进合同
  （"file-exists" → ["f","i","l","e",...]）；MCP 边界必须 fail-closed。
- goal/update：Goal 计划是长期承诺的权威状态，按 ADR-004 规则 6 收归
  Principal——模型客户端（client_id=mcp）调用必须 AUTH_FAILED。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest

from longtask.mcp_server import TOOLS, _validate_checks_argument
from longtask.rpc.errors import ErrorCode, RpcError


class TestStringChecksRejected:
    def test_bare_string_is_rejected_with_guidance(self) -> None:
        with pytest.raises(RpcError) as excinfo:
            _validate_checks_argument("file-exists")
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED
        msg = str(excinfo.value)
        assert "array" in msg
        assert '["file-exists"]' in msg, "error must show the correct call shape"

    def test_list_still_accepted(self) -> None:
        assert _validate_checks_argument(["file-exists:out.txt"]) == ["file-exists:out.txt"]

    def test_non_list_non_string_rejected(self) -> None:
        with pytest.raises(RpcError) as excinfo:
            _validate_checks_argument({"kind": "file-exists"})
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED

    def test_prepare_schema_declares_array(self) -> None:
        """schema 与边界校验必须同向：声明 array、执行拒非 array。"""
        schema = TOOLS["longtask_prepare_contract"][1]["inputSchema"]
        checks = schema["properties"]["acceptance_checks"]
        assert checks["type"] == "array"
        assert checks["items"]["type"] == "string"


class TestGoalUpdatePrincipalGate:
    def _conn(self, tmp_path) -> sqlite3.Connection:
        from lhgp.contracts.schema import Acceptance, Budget, ContractDraft
        from lhgp.persistence.schema import ensure_schema
        from lhgp.persistence.store import save_contract

        conn = sqlite3.connect(tmp_path / "state.db")
        ensure_schema(conn)
        save_contract(
            conn,
            contract_id="lt-gupd01",
            draft=ContractDraft(
                title="g",
                objective="o",
                deadline_at=datetime(2030, 1, 1, tzinfo=UTC),
                hard_constraints={},
                acceptance=Acceptance(standard="s", checks=("c1",)),
                workload_initial_hours=1.0,
                budget=Budget(
                    max_dispatches=3,
                    max_escalations=1,
                    max_concurrent_attempts=1,
                    max_attempt_minutes=30,
                    max_output_bytes=1048576,
                ),
            ),
            now=datetime.now(UTC),
            actor="user",
        )
        return conn

    def _update(self, conn: sqlite3.Connection, client_id: str) -> dict[str, Any]:
        from lhgp import PROTOCOL_VERSION
        from longtask.rpc.handlers.goal import handle_goal_update
        from longtask.rpc.methods import Method
        from longtask.rpc.server import RequestEnvelope

        env = RequestEnvelope(
            method=Method.GOAL_UPDATE,
            request_id=f"req-gupd-{client_id}",
            client_id=client_id,
            protocol_version=PROTOCOL_VERSION,
            params={
                "goal_id": "lt-gupd01",
                "revision": 1,
                "plan": {"stages": [{"id": "s1"}]},
            },
        )
        return handle_goal_update(env, conn=conn, now=datetime.now(UTC))

    def test_model_client_cannot_update_goal(self, tmp_path) -> None:
        conn = self._conn(tmp_path)
        try:
            with pytest.raises(RpcError) as excinfo:
                self._update(conn, client_id="mcp")
            assert "Principal" in str(excinfo.value)
        finally:
            conn.close()

    def test_user_client_can_update_goal(self, tmp_path) -> None:
        conn = self._conn(tmp_path)
        try:
            resp = self._update(conn, client_id="longtask-cli")
            assert resp["ok"] is True
        finally:
            conn.close()


class TestApproveToolContract:
    def test_approve_tool_sends_expected_revision_key(self) -> None:
        """工具面审计 R1 先例回归：CAS 键名必须与 handler 读取一致。

        handler 读 params["expected_revision"]；工具曾发 "revision"
        导致 MCP 路径 CAS 永不生效。锁定转发键名。
        """
        import inspect

        src = inspect.getsource(TOOLS["longtask_approve_contract"][0])
        assert '"expected_revision": args.get("revision")' in src
        assert '"revision": args.get("revision")' not in src
