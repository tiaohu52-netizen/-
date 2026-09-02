"""MCP server 集成测试（DESIGN §11.1、§17）。

真实 stdio 通信：spawn `longtask-mcp` 子进程，line-delimited JSON-RPC。
覆盖：协议握手（initialize / tools.list）、核心工具调通
（health / list_executors / prepare_contract / approve / get / attach_to_executor）、
错误处理（unknown tool / invalid args → JSON-RPC error）。

MCP 薄层对模型的意义：任何支持 MCP 的 agent harness（Claude Desktop、
agent-zero、agent-cli 等）只需 `longtask-mcp` 一个 stdio 入口就能让模型
发现并使用协议，无需自己解析 CLI。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration


def _spawn_mcp(data_dir: Path) -> subprocess.Popen[bytes]:
    """启动 longtask-mcp 子进程，stdio 用 bytes 收发。"""
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "longtask.mcp_server", "--data-dir", str(data_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=Path(__file__).resolve().parents[2],  # 仓库根，src 在 PYTHONPATH 之外
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )


def _roundtrip(
    proc: subprocess.Popen[bytes], method: str, params: dict[str, Any] | None = None, _id: int = 1
) -> dict[str, Any]:
    assert proc.stdin is not None and proc.stdout is not None
    req = {"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}
    proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
    proc.stdin.flush()
    line = proc.stdout.readline().decode("utf-8").strip()
    return json.loads(line)


def _result_text(resp: dict[str, Any]) -> dict[str, Any]:
    """MCP tools/call 响应：content[0].text 是 JSON 字符串。"""
    assert "result" in resp, f"error response: {resp}"
    text = resp["result"]["content"][0]["text"]
    return json.loads(text)


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """每个测试一个临时数据目录（不污染 ~/.longtask）。"""
    d = tmp_path / "mcp-data"
    d.mkdir()
    return d


class TestMCPDiscovery:
    """MCP 协议握手：模型先调 initialize / tools.list 来发现可用工具。"""

    def test_initialize_and_list_tools(self, data_dir: Path) -> None:
        proc = _spawn_mcp(data_dir)
        try:
            init = _roundtrip(
                proc,
                "initialize",
                {"protocolVersion": "2024-11-05", "clientInfo": {"name": "test"}},
            )
            assert init["result"]["serverInfo"]["name"] == "longtask-mcp"
            tools = _roundtrip(proc, "tools/list", {}, _id=2)
            names = {t["name"] for t in tools["result"]["tools"]}
            expected = {
                "longtask_health",
                "longtask_list_executors",
                "longtask_prepare_contract",
                "longtask_approve_contract",
                "longtask_get_contract",
                "longtask_list_contracts",
                "longtask_attach_to_executor",
            }
            assert expected.issubset(names)
            assert {
                "lhgp_prepare_goal",
                "lhgp_attempt_status",
                "lhgp_interrupt_attempt",
                "lhgp_write_back",
                "lhgp_notifications",
            }.issubset(names)
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestMCPHealth:
    def test_health_returns_protocol_info(self, data_dir: Path) -> None:
        proc = _spawn_mcp(data_dir)
        try:
            resp = _roundtrip(proc, "tools/call", {"name": "longtask_health", "arguments": {}})
            result = _result_text(resp)
            assert result["status"] == "ok"
            assert "protocol_version" in result
            assert "longtask_prepare_contract" in result["tools"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestMCPLifecycle:
    """AI 工具链完整走一遍：立合同 → 批准 → 拉起执行者（认领 attempt）。"""

    def test_prepare_approve_get_and_attach(self, data_dir: Path) -> None:
        proc = _spawn_mcp(data_dir)
        try:
            cid = "lt-20260901-mcp01"
            now = datetime.now(UTC)
            deadline = (now + timedelta(hours=2)).isoformat()

            # 1. 立合同
            resp = _roundtrip(
                proc,
                "tools/call",
                {
                    "name": "longtask_prepare_contract",
                    "arguments": {
                        "title": "MCP e2e 测试",
                        "objective": "让 MCP 走通立合同-批准-执行者认领三步",
                        "deadline_at": deadline,
                        "acceptance_standard": "合同进入 active 且执行者读得到上下文",
                        "acceptance_checks": (
                            "contract_id == lt-mcp-01",
                            "state == active after approve",
                            "执行者读到 active.md 全文",
                        ),
                        "hard_constraints": {
                            "file_effects": {
                                "mode": "workspace-write",
                                "workspace_root": str(data_dir / "ws"),
                            }
                        },
                        "workload_initial_hours": 1.5,
                        "contract_id": cid,
                    },
                },
                _id=10,
            )
            prepared = _result_text(resp)
            view = prepared.get("result", prepared)
            assert view.get("contract_id") == cid

            # 2. 批准
            resp = _roundtrip(
                proc,
                "tools/call",
                {
                    "name": "longtask_approve_contract",
                    "arguments": {"contract_id": cid},
                },
                _id=11,
            )
            approved = _result_text(resp)
            assert approved.get("result", {}).get("ok") is True or "state" in str(approved)

            # 3. 查询状态
            resp = _roundtrip(
                proc,
                "tools/call",
                {"name": "longtask_get_contract", "arguments": {"contract_id": cid}},
                _id=12,
            )
            view = _result_text(resp)
            # 视图里 state 应为 active
            state = view.get("state") or view.get("result", {}).get("state")
            assert state == "active", f"expected active, got {state}: {view}"

            # 4. 执行者认领：先手工给 attempt 一个 lease + 写入
            # （完整执行者收尾链路在 AttemptRunner 测试里覆盖；这里验证
            # MCP 工具能读 context/snapshot 而非写回——纯读 path）
            from longtask.persistence.store import (
                acquire_lease,
                connect,
                ensure_schema,
            )

            conn = connect(
                __import__("longtask.persistence.store", fromlist=["StoreConfig"]).StoreConfig(
                    db_path=data_dir / "state.db"
                )
            )
            ensure_schema(conn)
            aid = "att-mcp-test"
            acquire_lease(
                conn,
                contract_id=cid,
                holder_attempt_id=aid,
                expected_generation=0,
                heartbeat_at=now,
                timeout=timedelta(minutes=30),
            )
            conn.close()

            # 现在通过 MCP 走一轮 daemon tick（间接通过 attach_to_executor 走 status）
            resp = _roundtrip(
                proc,
                "tools/call",
                {
                    "name": "longtask_attach_to_executor",
                    "arguments": {
                        "contract_id": cid,
                        "attempt_id": aid,
                        # 不带 report_state = 仅读 attempt/status + 上下文快照
                    },
                },
                _id=13,
            )
            attached = _result_text(resp)
            assert attached["status"]["lease"]["holder_attempt_id"] == aid
            assert attached["status"]["lease"]["is_alive"] is True
            # §4.1 上下文快照：fixture 未派工，路径只回 hint（无 active.md）
            snapshot = attached["snapshot"]
            assert "hint" in snapshot
            assert snapshot.get("active_content") is None
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestMCPErrors:
    """错误处理：未知工具 / 缺必填 / RPC 错误都按 JSON-RPC 规范回 error 对象。"""

    def test_unknown_tool_returns_error(self, data_dir: Path) -> None:
        proc = _spawn_mcp(data_dir)
        try:
            resp = _roundtrip(
                proc,
                "tools/call",
                {"name": "longtask_nonexistent", "arguments": {}},
            )
            assert "error" in resp
            assert resp["error"]["code"] == -32602
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_missing_required_argument(self, data_dir: Path) -> None:
        proc = _spawn_mcp(data_dir)
        try:
            resp = _roundtrip(
                proc,
                "tools/call",
                {"name": "longtask_get_contract", "arguments": {}},
            )
            assert "error" in resp
            assert resp["error"]["code"] == -32602
        finally:
            proc.terminate()
            proc.wait(timeout=5)
