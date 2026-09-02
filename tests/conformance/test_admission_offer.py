"""P2 admission offer 一致性场景（ROADMAP P2 验收 #1/#2/#11 起步）。

覆盖：
- goal/prepare 走 validate_draft 单一 validator：缺字段抛 VALIDATION_FAILED
- goal/prepare 返回结构含 7 类 admission 字段（SPEC §10.4）
- 三维 allowlist 关闭时（authority.executor_policy=closed，空 executors）
  没有候选可通过——admission.eligible_executors 为空
- request_id 幂等：相同 request_id 重放不重复落库
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from longtask.persistence.store import (
    StoreConfig,
    connect,
    ensure_schema,
)
from longtask.rpc.errors import ErrorCode, RpcError
from longtask.rpc.handlers.goal import handle_goal_prepare
from longtask.rpc.methods import Method
from longtask.rpc.server import RequestEnvelope

pytestmark = pytest.mark.conformance

NOW = datetime(2026, 9, 1, 22, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=4)


def _draft_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "goal/prepare 测试",
        "objective": "验证 admission offer 7 字段",
        "deadline_at": LATER.isoformat(),
        "hard_constraints": {"file_effects": {"mode": "workspace-write"}},
        "acceptance": {
            "standard": "通过",
            "checks": ["result.txt 存在"],
            "verifier": "cross_check",
        },
        "workload_estimate": {"initial_hours": 1.0},
        "budget": {
            "max_dispatches": 3,
            "max_escalations": 1,
            "max_concurrent_attempts": 1,
            "max_attempt_minutes": 60,
            "max_output_bytes": 1048576,
        },
    }
    base.update(overrides)
    return base


def _envelope(request_id: str, params: dict[str, object]) -> RequestEnvelope:
    return RequestEnvelope(
        method=Method.GOAL_PREPARE,
        request_id=request_id,
        client_id="mcp",
        protocol_version=2,
        params=params,
    )


def _conn(tmp_path) -> sqlite3.Connection:
    db = tmp_path / "state.db"
    c = connect(StoreConfig(db_path=db))
    ensure_schema(c)
    return c


def test_goal_prepare_returns_offer_with_seven_fields(tmp_path) -> None:
    """goal/prepare 返回结构含 SPEC §10.4 全部 7 类 admission 字段。"""
    conn = _conn(tmp_path)
    params = {"draft": _draft_payload()}
    env = _envelope("req-1", params)
    resp = handle_goal_prepare(env, conn=conn, now=NOW)
    assert resp["ok"] is True
    result = resp["result"]
    assert "contract" in result
    assert "admission" in result
    adm = result["admission"]
    # 7 字段全部存在
    for key in (
        "eligible_executors",
        "rejected_executors",
        "acceptance_executable",
        "forecast_p50_minutes",
        "forecast_p90_minutes",
        "forecast_confidence",
        "verification_reserve_sufficient",
        "safe_start_by",
        "uncontrolled_risks",
        "declared_guarantees",
    ):
        assert key in adm, f"missing admission key: {key}"
    # 缺执行器注册表 → 候选列表都空
    assert adm["eligible_executors"] == []
    assert adm["rejected_executors"] == []
    # 验收可执行（有 checks）
    assert adm["acceptance_executable"] is True


def test_offer_with_forecast_requires_safe_start_by() -> None:
    """已产生 p90 预测但无法证明安全启动时刻时必须拒绝 admission。"""
    from longtask.admission.offer import ExecutorCandidateView, Offer

    candidate = ExecutorCandidateView(executor_id="exec-a", models=("*",), reason="ok")
    offer = Offer(
        eligible_executors=(candidate,),
        acceptance_executable=True,
        verification_reserve_sufficient=True,
        forecast_p90_minutes=120.0,
    )
    assert offer.eligible is False


def test_goal_prepare_closed_authority_no_eligible(tmp_path) -> None:
    """§6.1 默认 authority.executor_policy=closed、空 executors → 无候选可通过。"""
    conn = _conn(tmp_path)
    params = {"draft": _draft_payload()}
    env = _envelope("req-closed", params)
    resp = handle_goal_prepare(env, conn=conn, now=NOW)
    assert resp["ok"] is True
    adm = resp["result"]["admission"]
    # 默认 closed + 空 executors：调用方未注入 registry，故 offer 中没有候选
    # 这是设计意图：caller 显式注入 registry.snapshot_for_admission 才会填充
    assert adm["eligible_executors"] == []
    assert adm["rejected_executors"] == []


def test_goal_prepare_validates_draft_via_single_validator(tmp_path) -> None:
    """缺必填字段（deadline_at）必须由 validate_draft 拦下（§22 单一 validator）。"""
    conn = _conn(tmp_path)
    bad = _draft_payload()
    del bad["deadline_at"]
    params = {"draft": bad}
    env = _envelope("req-bad", params)
    with pytest.raises(RpcError) as exc:
        handle_goal_prepare(env, conn=conn, now=NOW)
    assert exc.value.code == ErrorCode.VALIDATION_FAILED
    assert "deadline_at" in exc.value.message


def test_goal_prepare_idempotent_replay_does_not_duplicate(tmp_path) -> None:
    """同 request_id 重放不重复落库。"""
    conn = _conn(tmp_path)
    params = {"draft": _draft_payload()}
    env1 = _envelope("req-replay", params)
    r1 = handle_goal_prepare(env1, conn=conn, now=NOW)
    cid1 = r1["result"]["contract"]["contract_id"]
    # 第二次同 request_id 应走幂等返回原合同
    r2 = handle_goal_prepare(env1, conn=conn, now=NOW)
    cid2 = r2["result"]["contract"]["contract_id"]
    assert cid1 == cid2


def test_goal_prepare_with_registry_passes_executor_through_seven_conditions(tmp_path) -> None:
    """注入 registry → snapshot_for_admission → admission 填充候选。

    验证：executors.allowlist 显式允许 exec-a → 候选通过 7 条件；未允许
    exec-b → 候选被拒（§6.3 condition 2 失败）。
    """
    from longtask.adapters.manifest import (
        Capabilities,
        Enforcement,
        ExecutorManifest,
        SandboxCapability,
    )
    from longtask.adapters.registry import ExecutorRegistry, RegistryEntry

    conn = _conn(tmp_path)

    # 构造两个执行器 a / b，a 在 allowlist、b 不在
    sandbox = SandboxCapability(
        file_effects="workspace-write",
        network="deny",
        process="restricted",
        enforcement=Enforcement.FULL,
    )
    manifest_a = ExecutorManifest(
        executor_id="exec-a",
        adapter_version="0.1.0",
        transport="subprocess",
        capabilities=Capabilities(
            spawn=True,
            observe=True,
            cancel=True,
            notify=True,
            followup=True,
            steer=True,
            interrupt=True,
            context="required",
            sandbox=sandbox,
            acceptance_evidence=True,
        ),
    )
    manifest_b = ExecutorManifest(
        executor_id="exec-b",
        adapter_version="0.1.0",
        transport="subprocess",
        capabilities=Capabilities(
            spawn=True,
            observe=True,
            cancel=True,
            notify=True,
            followup=True,
            steer=True,
            interrupt=True,
            context="required",
            sandbox=sandbox,
            acceptance_evidence=True,
        ),
    )
    reg = ExecutorRegistry(
        [
            RegistryEntry(
                id="exec-a",
                kind="subprocess",
                launch=None,
                capabilities=manifest_a.capabilities,
                limits={},
                cost_hint="low",
                enabled=True,
            ),
            RegistryEntry(
                id="exec-b",
                kind="subprocess",
                launch=None,
                capabilities=manifest_b.capabilities,
                limits={},
                cost_hint="low",
                enabled=True,
            ),
        ]
    )

    # contract authority 显式 allow exec-a
    draft = _draft_payload()
    draft["authority"] = {
        "executor_policy": "explicit_allow",
        "executors": [{"executor_id": "exec-a", "models": ["*"], "roles": ["executor"]}],
    }

    resp = handle_goal_prepare(
        _envelope("req-reg", {"draft": draft}),
        conn=conn,
        now=NOW,
        registry=reg,
    )
    assert resp["ok"] is True
    adm = resp["result"]["admission"]
    eligible = [c["executor_id"] for c in adm["eligible_executors"]]
    rejected = [c["executor_id"] for c in adm["rejected_executors"]]
    assert "exec-a" in eligible
    assert "exec-b" in rejected
