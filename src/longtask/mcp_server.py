"""MCP server 薄层（DESIGN §11.1、§17）：让任何 MCP 兼容的 agent harness 能
「发现并使用」longtask 协议。

设计选择：暴露一组**面向 AI 任务流**的工具（不是把底层 JSON-RPC 方法
一对一透传——那只是 RPC 隧道，不是 AI 接口）。工具覆盖探活、诊断、目标/合同
生命周期、执行器接力、验收请求、审计与控制，模型可以独立走完
"立合同→批准→交付→验收"四步；规范命名与兼容命名双轨并存：

- longtask_health: 协议探活 + 返回可用工具清单（mcp discoverable 钩子）
- longtask_list_executors: 选哪个执行器
- longtask_prepare_contract: 立合同（填 objective / acceptance.checks /
  hard_constraints / deadline —— 详见 skills/longtask-contract/SKILL.md）
- longtask_approve_contract: 批准进入调度
- longtask_get_contract / longtask_list_contracts: 跟踪状态
- longtask_attach_to_executor: 模型作为执行者被协议拉起时，认领
  attempt 并写回（attempt/status + attempt/write-back 包装；lease/renew
  留接口未自动包，由执行者侧 RPC 单独调用以保留心跳节流）

传输：stdio JSON-RPC 2.0 文本协议（line-delimited JSON，标准 MCP
transport）。零新增三方依赖：标准库 json + asyncio（readline 阻塞模式
更轻，AI 工具链路用 stream 模式不必要）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from longtask import PROTOCOL_VERSION, __version__
from longtask.adapters.registry import ExecutorRegistry
from longtask.cli.doctor import run_doctor
from longtask.cli.paths import default_data_root
from longtask.persistence.notifications import list_notifications
from longtask.persistence.store import (
    StoreConfig,
    connect,
    ensure_schema,
)
from longtask.rpc.errors import RpcError
from longtask.rpc.methods import Method
from longtask.rpc.server import parse_envelope, route


def _now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


def _mcp_request_id(method: Method, args: dict[str, Any]) -> str:
    """Return an explicit or stable derived request key for MCP retries.

    MCP tool calls do not expose the transport request id to the wrapped RPC
    handler.  A canonical digest keeps an omitted-id retry idempotent while an
    explicit key still lets callers intentionally distinguish identical calls.
    """
    explicit = args.get("request_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    payload = {key: value for key, value in args.items() if key != "request_id"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    return f"mcp:{method.value}:{digest}"


# ─── MCP 工具：每个工具对应一个 JSON-RPC method + 入参映射 ─────────────


def tool_health(_args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "implementation_version": __version__,
        "status": "ok",
        "tools": TOOL_NAMES,
    }


def tool_doctor(_args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Run local preflight diagnostics without changing contract state."""
    report = run_doctor(ctx["root"])
    return {
        "all_ok": report.all_ok,
        "protocol_version": report.protocol_version,
        "package_version": report.package_version,
        "checks": [
            {"name": check.name, "ok": check.ok, "message": check.message, "details": check.details}
            for check in report.checks
        ],
    }


def tool_list_executors(_args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """可用的执行器池（用户框定后）：模型先看这个再选执行器。"""
    reg = ctx["registry"]
    enabled_only = bool(_args.get("enabled_only", False))
    entries = reg.list_entries(enabled_only=enabled_only)
    return {
        "executors": [
            {
                "id": e.id,
                "kind": e.kind,
                "enabled": e.enabled,
                "capabilities": e.capabilities.to_dict()
                if hasattr(e.capabilities, "to_dict")
                else {},
                "cost_hint": str(
                    e.cost_hint.value if hasattr(e.cost_hint, "value") else e.cost_hint
                ),
            }
            for e in entries
        ]
    }


def tool_prepare_contract(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """立远期合同。详见 skills/longtask-contract/SKILL.md §4。"""
    payload: dict[str, Any] = {
        "title": args["title"],
        "objective": args["objective"],
        "deadline_at": args["deadline_at"],
        "hard_constraints": args.get("hard_constraints", {}),
        "acceptance": {
            "standard": args["acceptance_standard"],
            "checks": list(args["acceptance_checks"]),
        },
        "workload_estimate": {"initial_hours": float(args.get("workload_initial_hours", 1.0))},
        "budget": args.get(
            "budget",
            {
                "max_dispatches": 5,
                "max_escalations": 1,
                "max_concurrent_attempts": 1,
                "max_attempt_minutes": 60,
                "max_output_bytes": 1048576,
            },
        ),
        "authority": args.get("authority", {}),
        "attention": args.get("attention", {}),
        "continuity": args.get("continuity", {}),
        "context": args.get("context", {}),
        "execution": args.get("execution", {}),
        "client_meta": args.get("client_meta", {}),
    }
    params: dict[str, Any] = {"draft": payload}
    # contract_id 提到 envelope params（与 handle_contract_prepare 的入参对齐）
    if args.get("contract_id"):
        params["contract_id"] = args["contract_id"]
    envelope = parse_envelope(
        {
            "method": Method.CONTRACT_PREPARE.value,
            "request_id": _mcp_request_id(Method.CONTRACT_PREPARE, args),
            "client_id": args.get("client_id", "mcp"),
            "protocol_version": PROTOCOL_VERSION,
            "params": params,
        }
    )
    return route(envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"])


def tool_approve_contract(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    envelope = parse_envelope(
        {
            "method": Method.CONTRACT_APPROVE.value,
            "request_id": _mcp_request_id(Method.CONTRACT_APPROVE, args),
            "client_id": "mcp",
            "protocol_version": PROTOCOL_VERSION,
            "params": {
                "contract_id": args["contract_id"],
                "revision": args.get("revision"),
            },
        }
    )
    return route(envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"])


def tool_request_verification(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """用户触发验收（§12.4）：只派 verifier，不派执行者。"""
    envelope = parse_envelope(
        {
            "method": Method.CONTRACT_REQUEST_VERIFICATION.value,
            "request_id": _mcp_request_id(Method.CONTRACT_REQUEST_VERIFICATION, args),
            "client_id": "mcp",
            "protocol_version": PROTOCOL_VERSION,
            "params": {"contract_id": args["contract_id"]},
        }
    )
    return route(envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"])


def tool_get_contract(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    envelope = parse_envelope(
        {
            "method": Method.CONTRACT_GET.value,
            "request_id": _mcp_request_id(Method.CONTRACT_GET, args),
            "client_id": "mcp",
            "protocol_version": PROTOCOL_VERSION,
            "params": {
                "contract_id": args["contract_id"],
                "decision_limit": args.get("decision_limit", 50),
                "attempt_limit": args.get("attempt_limit", 20),
            },
        }
    )
    return route(envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"])


def tool_list_contracts(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    envelope = parse_envelope(
        {
            "method": Method.CONTRACT_LIST.value,
            "request_id": _mcp_request_id(Method.CONTRACT_LIST, args),
            "client_id": "mcp",
            "protocol_version": PROTOCOL_VERSION,
            "params": {
                "state": args.get("state"),
                "limit": args.get("limit", 20),
            },
        }
    )
    return route(envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"])


def tool_get_goal(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Read a stable Goal aggregate, independent of a contract revision."""
    return _mcp_route(Method.GOAL_GET, args, ctx)


def tool_list_goals(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """List stable Goal aggregates."""
    return _mcp_route(Method.GOAL_LIST, args, ctx)


def tool_update_goal(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """CAS-update a Goal's long-lived plan or progress."""
    return _mcp_route(Method.GOAL_UPDATE, args, ctx)


def tool_advance_goal(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Complete the current Goal stage with revision CAS."""
    return _mcp_route(Method.GOAL_ADVANCE, args, ctx)


def tool_next_goal_action(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Get the next safe model action for a Goal."""
    return _mcp_route(Method.GOAL_NEXT, args, ctx)


def tool_goal_contract_draft(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Build a reviewable contract draft from a Goal stage."""
    return _mcp_route(Method.GOAL_CONTRACT_DRAFT, args, ctx)


def _mcp_route(method: Method, args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """为控制类工具构造统一的模型侧 RPC envelope。"""
    return route(
        parse_envelope(
            {
                "method": method.value,
                "request_id": _mcp_request_id(method, args),
                "client_id": "mcp",
                "protocol_version": PROTOCOL_VERSION,
                "params": dict(args),
            }
        ),
        conn=ctx["conn"],
        now=_now(),
        registry=ctx["registry"],
    )


def tool_attempt_status(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """读取执行 attempt 的事件、租约和当前状态。"""
    return _mcp_route(Method.ATTEMPT_STATUS, args, ctx)


def tool_notifications(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """只读通知队列状态；默认不返回 payload 内容。"""
    status = args.get("status")
    goal_id = args.get("goal_id")
    if status is not None and not isinstance(status, str):
        raise ValueError("status must be a string")
    if goal_id is not None and not isinstance(goal_id, str):
        raise ValueError("goal_id must be a string")
    raw_limit = args.get("limit", 50)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        raise ValueError("limit must be an integer")
    limit = raw_limit
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    include_payload = bool(args.get("include_payload", False))
    rows = list_notifications(
        ctx["conn"],
        status=status or None,
        goal_id=goal_id or None,
        limit=limit,
    )
    return {
        "notifications": [
            {
                "notification_id": item.notification_id,
                "idempotency_key": item.idempotency_key,
                "goal_id": item.goal_id,
                "event_type": item.event_type,
                "channel": item.channel,
                "status": item.status,
                "attempts": item.attempts,
                "available_at": item.available_at.isoformat(),
                "last_error": item.last_error,
                **({"payload": item.payload} if include_payload else {}),
            }
            for item in rows
        ]
    }


def tool_interrupt_attempt(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """排队中断执行中的 attempt，由 daemon 兑现实际取消。"""
    return _mcp_route(Method.CONTROL_INTERRUPT, args, ctx)


def tool_write_back(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """写回进度、终态、结构化验收证据和实际模型身份。"""
    return _mcp_route(Method.ATTEMPT_WRITE_BACK, args, ctx)


def tool_attach_to_executor(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """执行者侧：模型被协议拉起时认领自己的 attempt 上下文 + 报告结果。

    流程（合并多次 RPC 调用，AI 一次工具调用完成）：
    1. 读自己的 attempt/status 拿快照（active.md 路径、租约代次）；
    2. 展示 active.md 全文（让模型见到 §4.1 上下文）；
    3. 可选：调用 attempt/write-back 报告 succeeded|failed + 进度；
    4. 可选：调用 lease/renew 续心跳（节流到默认 60s，模型按需调）。

    主动权在模型：模型读完上下文后自己选择继续干还是上报结果。
    """
    contract_id = args["contract_id"]
    attempt_id = args["attempt_id"]
    report_state = args.get("report_state")
    progress_note = args.get("progress_note")

    # 1. 读 attempt 状态（事件史 + 租约）
    status_envelope = parse_envelope(
        {
            "method": Method.ATTEMPT_STATUS.value,
            "request_id": _mcp_request_id(Method.ATTEMPT_STATUS, args),
            "client_id": "mcp",
            "protocol_version": PROTOCOL_VERSION,
            "params": {"contract_id": contract_id, "attempt_id": attempt_id},
        }
    )
    status = route(status_envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"]).get(
        "result", {}
    )

    # 2. 读上下文快照
    snapshot = _read_snapshot(ctx, status)
    out: dict[str, Any] = {
        "status": status,
        "snapshot": snapshot,
    }

    # 3. 报告结果（如有）
    if report_state in ("succeeded", "failed"):
        # 严格 fencing：写回需 write_generation（代次），从 status.lease.generation 取
        generation = (status.get("lease") or {}).get("generation")
        if generation is None:
            out["write_back_error"] = "no live lease; cannot write back"
        else:
            wb_envelope = parse_envelope(
                {
                    "method": Method.ATTEMPT_WRITE_BACK.value,
                    "request_id": _mcp_request_id(Method.ATTEMPT_WRITE_BACK, args),
                    "client_id": "mcp",
                    "protocol_version": PROTOCOL_VERSION,
                    "params": {
                        "contract_id": contract_id,
                        "attempt_id": attempt_id,
                        "write_generation": generation,
                        "attempt_state": report_state,
                        "progress_note": progress_note or "",
                    },
                }
            )
            try:
                wb = route(wb_envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"])
                out["write_back"] = wb.get("result", wb)
            except RpcError as exc:
                out["write_back_error"] = str(exc)
    elif progress_note and not report_state:
        # 纯进度更新：写 attempt/write-back 但 attempt_state 不传
        # → 由 RPC handler 视作无终态；安全。或不写终态传 progress_note 即可。
        generation = (status.get("lease") or {}).get("generation")
        if generation is not None:
            wb_envelope = parse_envelope(
                {
                    "method": Method.ATTEMPT_WRITE_BACK.value,
                    "request_id": _mcp_request_id(Method.ATTEMPT_WRITE_BACK, args),
                    "client_id": "mcp",
                    "protocol_version": PROTOCOL_VERSION,
                    "params": {
                        "contract_id": contract_id,
                        "attempt_id": attempt_id,
                        "write_generation": generation,
                        "progress_note": progress_note,
                    },
                }
            )
            try:
                out["progress_written"] = route(
                    wb_envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"]
                ).get("result")
            except RpcError as exc:
                out["progress_error"] = str(exc)
    return out


def _read_snapshot(ctx: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    """从 attempt/status 的 result 找 contract，再读 context/attempts/<id>/active.md。"""
    contract_id = status.get("contract_id")
    if not contract_id:
        return {}
    root: Path = ctx["root"]
    contract_dir = root / "contracts" / contract_id
    if not contract_dir.is_dir():
        return {"hint": "no contract projection yet (try after first tick)"}
    # 找最近 attempt 的 context/attempts/<id>/active.md
    context_dir = contract_dir / "context" / "attempts"
    if not context_dir.is_dir():
        return {
            "hint": "no context snapshot built yet (§4.1 required=true only)",
            "handover_path": str(contract_dir / "handover.md"),
        }
    # 最新 attempt 目录
    attempts = sorted(context_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
    if not attempts:
        return {"handover_path": str(contract_dir / "handover.md")}
    active = attempts[0] / "active.md"
    return {
        "active_path": str(active),
        "active_content": active.read_text(encoding="utf-8", errors="replace")
        if active.is_file()
        else None,
        "handover_path": str(contract_dir / "handover.md"),
        "handover_content": (contract_dir / "handover.md").read_text(
            encoding="utf-8", errors="replace"
        )
        if (contract_dir / "handover.md").is_file()
        else None,
    }


TOOLS: dict[
    str, tuple[Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]], dict[str, Any]]
] = {
    "longtask_health": (
        tool_health,
        {
            "description": "探活：返回协议版本、协议方法清单、可用工具名。模型第一步调用。",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ),
    "longtask_doctor": (
        tool_doctor,
        {
            "description": "运行本机只读诊断，检查数据库、注册表和已启用 CLI 是否可启动。",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ),
    "longtask_list_executors": (
        tool_list_executors,
        {
            "description": (
                "列出可用的执行器（用户框定后），可按 enabled_only 过滤。"
                "准备合同时选执行器前先看这个。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "enabled_only": {"type": "boolean", "default": False},
                },
            },
        },
    ),
    "longtask_prepare_contract": (
        tool_prepare_contract,
        {
            "description": (
                "立远期合同。详见 skills/longtask-contract/SKILL.md §4。\n"
                "objective 写验收不是方法；acceptance_checks 逐条可核对；"
                "workload_initial_hours 如实填（u=workload/time_left 决定派工）。"
            ),
            "inputSchema": {
                "type": "object",
                "required": [
                    "title",
                    "objective",
                    "deadline_at",
                    "acceptance_standard",
                    "acceptance_checks",
                ],
                "properties": {
                    "title": {"type": "string"},
                    "objective": {
                        "type": "string",
                        "description": "写验收（产出的结果），不是写方法",
                    },
                    "deadline_at": {
                        "type": "string",
                        "description": "ISO 8601（必含时区），如 2026-09-12T18:00:00+08:00",
                    },
                    "acceptance_standard": {"type": "string"},
                    "acceptance_checks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "逐条可独立核对的子项；verifier 照着判",
                    },
                    "hard_constraints": {
                        "type": "object",
                        "default": {},
                        "description": (
                            "如 {file_effects: {mode: workspace-write, workspace_root: /abs/path}}"
                        ),
                    },
                    "workload_initial_hours": {"type": "number", "default": 1.0},
                    "budget": {
                        "type": "object",
                        "description": "可选覆盖默认预算",
                    },
                    "authority": {
                        "type": "object",
                        "description": "允许被唤起的 executor、model 与 role 绑定",
                    },
                    "attention": {"type": "object"},
                    "continuity": {"type": "object"},
                    "context": {"type": "object"},
                    "execution": {"type": "object"},
                    "contract_id": {"type": "string", "description": "可选自定义 ID"},
                    "request_id": {
                        "type": "string",
                        "description": "幂等重试键；重试同一变更时必须复用",
                    },
                },
            },
        },
    ),
    "longtask_approve_contract": (
        tool_approve_contract,
        {
            "description": "批准合同（drafted → active），协议开始调度。",
            "inputSchema": {
                "type": "object",
                "required": ["contract_id"],
                "properties": {
                    "contract_id": {"type": "string"},
                    "revision": {"type": "integer", "description": "CAS 期望版本号（可选）"},
                    "request_id": {"type": "string", "description": "幂等重试键；重试时复用"},
                },
            },
        },
    ),
    "longtask_request_verification": (
        tool_request_verification,
        {
            "description": (
                "用户直接请求验收（§12.4）：不派执行者，只派独立 verifier 核对"
                "现有交付物。典型场景：执行预算耗尽（blocked）但交付物疑似已"
                "就绪——先看看现状算不算完成。验证预算耗尽或已有 verifier 在"
                "跑时如实拒绝。"
            ),
            "inputSchema": {
                "type": "object",
                "required": ["contract_id"],
                "properties": {
                    "contract_id": {"type": "string"},
                    "request_id": {"type": "string", "description": "幂等重试键；重试时复用"},
                },
            },
        },
    ),
    "longtask_get_contract": (
        tool_get_contract,
        {
            "description": (
                "查询单份合同权威视图（§11.6 字段表），并返回该合同隔离的"
                " decision_history、attempt_history 与 verification_history；可用"
                " decision_limit / attempt_limit 控制上下文大小。响应还包含最新"
                " deadline_snapshot（forecast、slack、risk、confidence 与"
                " next_decision_at），供模型直接判断下一步动作；"
                " verification_history 展示验收请求、消费和 verifier 启动状态。"
            ),
            "inputSchema": {
                "type": "object",
                "required": ["contract_id"],
                "properties": {
                    "contract_id": {"type": "string"},
                    "decision_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                        "description": "返回该合同最近决策历史条数",
                    },
                    "attempt_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                        "description": "返回该合同最近 attempt 历史条数",
                    },
                },
            },
        },
    ),
    "longtask_list_contracts": (
        tool_list_contracts,
        {
            "description": (
                "按状态过滤列出合同；每项包含最新 deadline_snapshot，模型可先"
                "据此筛选风险目标，再决定是否读取完整合同历史。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "state": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 20,
                        "description": "最多返回 200 份合同，避免一次读取过大上下文",
                    },
                },
            },
        },
    ),
    "longtask_get_goal": (
        tool_get_goal,
        {
            "description": "查询稳定 Goal 聚合视图（合同历史、状态和 Deadline 风险）。",
            "inputSchema": {
                "type": "object",
                "required": ["goal_id"],
                "properties": {"goal_id": {"type": "string"}},
            },
        },
    ),
    "longtask_list_goals": (
        tool_list_goals,
        {
            "description": "列出稳定 Goal 聚合视图。",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 20}},
            },
        },
    ),
    "longtask_update_goal": (
        tool_update_goal,
        {
            "description": "更新 Goal 计划或进度（支持 revision CAS）。",
            "inputSchema": {
                "type": "object",
                "required": ["goal_id", "revision"],
                "properties": {
                    "goal_id": {"type": "string"},
                    "revision": {"type": "integer"},
                    "plan": {"type": "object"},
                    "progress": {"type": "object"},
                    "request_id": {"type": "string", "description": "幂等重试键；重试时复用"},
                },
            },
        },
    ),
    "longtask_advance_goal": (
        tool_advance_goal,
        {
            "description": "完成当前 Goal 阶段并推进到下一阶段。",
            "inputSchema": {
                "type": "object",
                "required": ["goal_id", "stage_id", "revision"],
                "properties": {
                    "goal_id": {"type": "string"},
                    "stage_id": {"type": "string"},
                    "revision": {"type": "integer"},
                    "request_id": {"type": "string", "description": "幂等重试键；重试时复用"},
                },
            },
        },
    ),
    "longtask_next_goal_action": (
        tool_next_goal_action,
        {
            "description": "读取 Goal 当前阶段的下一步可执行动作（只读）。",
            "inputSchema": {
                "type": "object",
                "required": ["goal_id"],
                "properties": {"goal_id": {"type": "string"}},
            },
        },
    ),
    "longtask_goal_contract_draft": (
        tool_goal_contract_draft,
        {
            "description": "根据 Goal 当前阶段生成合同草案（只读，不创建合同）。",
            "inputSchema": {
                "type": "object",
                "required": ["goal_id"],
                "properties": {
                    "goal_id": {"type": "string"},
                    "stage_id": {"type": "string"},
                },
            },
        },
    ),
    "longtask_attach_to_executor": (
        tool_attach_to_executor,
        {
            "description": (
                "执行者侧：认领 attempt + 读上下文快照 + 报告结果（合并的便利工具）。"
                "执行者被协议拉起时（task_prompt 含 objective+附言）调用："
                "返回 active.md 全文 + 交接摘要，模型可决定继续干还是上报 succeeded/failed。"
            ),
            "inputSchema": {
                "type": "object",
                "required": ["contract_id", "attempt_id"],
                "properties": {
                    "contract_id": {"type": "string"},
                    "attempt_id": {"type": "string"},
                    "report_state": {
                        "type": "string",
                        "enum": ["succeeded", "failed"],
                        "description": "如要上报结果就填；仅写进度则不填",
                    },
                    "progress_note": {
                        "type": "string",
                        "description": "进度要点（落 context/scratch-updated 事件）",
                    },
                    "request_id": {
                        "type": "string",
                        "description": "写回幂等重试键；重试同一报告时必须复用",
                    },
                },
            },
        },
    ),
}

# P6 新命名与旧命名双轨并存：旧工具保持可发现，新增工具使用协议名
# lhgp_*，避免升级时破坏已有 Agent 的工具缓存。
_RENAMED_TOOLS = {
    "lhgp_health": "longtask_health",
    "lhgp_doctor": "longtask_doctor",
    "lhgp_list_executors": "longtask_list_executors",
    "lhgp_prepare_goal": "longtask_prepare_contract",
    "lhgp_approve_goal": "longtask_approve_contract",
    "lhgp_get_goal": "longtask_get_goal",
    "lhgp_list_goals": "longtask_list_goals",
    "lhgp_update_goal": "longtask_update_goal",
    "lhgp_advance_goal": "longtask_advance_goal",
    "lhgp_next_goal_action": "longtask_next_goal_action",
    "lhgp_goal_contract_draft": "longtask_goal_contract_draft",
    "lhgp_attach_executor": "longtask_attach_to_executor",
    # 合同读取/列表此前只在遗留 longtask_* 命名空间暴露，只用 lhgp_* 规范
    # 工具的 AI 无法查看自己立下的合同，补齐以免规范工具集存在死角。
    "lhgp_get_contract": "longtask_get_contract",
    "lhgp_list_contracts": "longtask_list_contracts",
    "lhgp_request_verification": "longtask_request_verification",
}
for _new_name, _legacy_name in _RENAMED_TOOLS.items():
    _handler, _metadata = TOOLS[_legacy_name]
    TOOLS[_new_name] = (
        _handler,
        {**_metadata, "description": f"[LHGP] {_metadata['description']}"},
    )

TOOLS.update(
    {
        "lhgp_attempt_status": (
            tool_attempt_status,
            {
                "description": "读取 attempt 当前状态、事件历史和租约信息。",
                "inputSchema": {
                    "type": "object",
                    "required": ["contract_id", "attempt_id"],
                    "properties": {
                        "contract_id": {"type": "string"},
                        "attempt_id": {"type": "string"},
                    },
                },
            },
        ),
        "lhgp_notifications": (
            tool_notifications,
            {
                "description": "查看通知 outbox 的投递状态（只读）。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "status": {"enum": ["pending", "leased", "sent"]},
                        "goal_id": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                        "include_payload": {"type": "boolean"},
                    },
                },
            },
        ),
        "lhgp_interrupt_attempt": (
            tool_interrupt_attempt,
            {
                "description": "请求 daemon 中断指定 attempt。",
                "inputSchema": {
                    "type": "object",
                    "required": ["contract_id", "attempt_id"],
                    "properties": {
                        "contract_id": {"type": "string"},
                        "attempt_id": {"type": "string"},
                        "reason": {"type": "string"},
                        "request_id": {"type": "string", "description": "幂等重试键；重试时复用"},
                    },
                },
            },
        ),
        "lhgp_write_back": (
            tool_write_back,
            {
                "description": "写回进度、终态、验收 evidence 和实际 model_id。",
                "inputSchema": {
                    "type": "object",
                    "required": ["contract_id", "attempt_id", "write_generation"],
                    "properties": {
                        "contract_id": {"type": "string"},
                        "attempt_id": {"type": "string"},
                        "write_generation": {"type": "integer"},
                        "attempt_state": {"enum": ["succeeded", "failed"]},
                        "progress_note": {"type": "string"},
                        "model_id": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "object"}},
                        "request_id": {"type": "string", "description": "幂等重试键；重试时复用"},
                    },
                },
            },
        ),
    }
)

# MCP tool annotations are advisory metadata consumed by hosts before execution.
# Keep the policy explicit here so aliases and future tools cannot silently lose
# the local-only trust boundary or be presented as harmless reads.
_DESTRUCTIVE_TOOLS = {
    # Contract/Goal mutations.  ``destructiveHint`` is intentionally used for
    # any persistent state change, not only process termination: MCP hosts need
    # to request confirmation before a model edits the durable commitment.
    "longtask_prepare_contract",
    "longtask_approve_contract",
    "longtask_request_verification",
    "longtask_update_goal",
    "longtask_advance_goal",
    "longtask_attach_to_executor",
    "lhgp_approve_goal",
    "lhgp_prepare_goal",
    "lhgp_update_goal",
    "lhgp_advance_goal",
    "lhgp_attach_executor",
    "lhgp_request_verification",
    "lhgp_interrupt_attempt",
    "lhgp_write_back",
}
_READ_ONLY_TOOLS = {
    "longtask_health",
    "longtask_doctor",
    "longtask_list_executors",
    "longtask_get_contract",
    "longtask_list_contracts",
    "longtask_get_goal",
    "longtask_list_goals",
    "longtask_next_goal_action",
    "longtask_goal_contract_draft",
    "lhgp_health",
    "lhgp_doctor",
    "lhgp_list_executors",
    "lhgp_get_goal",
    "lhgp_list_goals",
    "lhgp_next_goal_action",
    "lhgp_goal_contract_draft",
    "lhgp_get_contract",
    "lhgp_list_contracts",
    "lhgp_attempt_status",
    "lhgp_notifications",
}
for _tool_name, (_tool_fn, _tool_schema) in list(TOOLS.items()):
    _tool_schema.setdefault(
        "annotations",
        {
            "readOnlyHint": _tool_name in _READ_ONLY_TOOLS,
            "destructiveHint": _tool_name in _DESTRUCTIVE_TOOLS,
            "openWorldHint": False,
        },
    )
TOOL_NAMES = sorted(TOOLS.keys())


# ─── JSON-RPC over stdio ────────────────────────────────────────────────────


def _make_response(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: Any, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _server_name() -> str:
    """Expose the canonical name when launched through the canonical entrypoint."""
    return "lhgp-mcp" if Path(sys.argv[0]).stem == "lhgp-mcp" else "longtask-mcp"


def _dispatch(ctx: dict[str, Any], method: str, params: Any, req_id: Any) -> dict[str, Any]:
    if method == "initialize":
        return _make_response(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": _server_name(), "version": __version__},
                "capabilities": {"tools": {}},
            },
        )
    if method == "ping":
        return _make_response(req_id, {})
    if method == "tools/list":
        return _make_response(
            req_id,
            {"tools": [{"name": name, **schema} for name, (_fn, schema) in TOOLS.items()]},
        )
    if method == "tools/call":
        if not isinstance(params, dict):
            return _make_error(req_id, -32602, "invalid arguments: params must be an object")
        tool_name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _make_error(req_id, -32602, "invalid arguments: arguments must be an object")
        if tool_name not in TOOLS:
            return _make_error(req_id, -32602, f"unknown tool: {tool_name}")
        try:
            fn, _schema = TOOLS[tool_name]
            result = fn(args, ctx)
            # MCP 要求 content 数组里至少一个 item
            return _make_response(
                req_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    ]
                },
            )
        except RpcError as exc:
            # 协议错误码语义比 JSON-RPC 数字更有意义；原样透传 StrEnum 值
            return _make_error(req_id, exc.code.value, str(exc.message), exc.details or None)
        except (KeyError, ValueError, TypeError) as exc:
            return _make_error(req_id, -32602, f"invalid arguments: {exc}")
        except Exception as exc:  # 兜底：避免错误透传污染 stdio
            return _make_error(req_id, -32603, f"internal error: {exc}")
    return _make_error(req_id, -32601, f"method not found: {method}")


def _make_context(root: Path) -> dict[str, Any]:
    """每个连接共享 root/conn/registry（这里一次性 init；未来可按请求切换）。"""
    conn = connect(StoreConfig(db_path=root / "state.db"))
    ensure_schema(conn)
    registry = ExecutorRegistry.load_from_file(root / "registry.json")
    return {"root": root, "conn": conn, "registry": registry}


def serve_stdio(root: Path) -> None:
    """stdio JSON-RPC 入口：读一行 JSON、写一行 JSON（标准 MCP transport）。

    输出始终 ASCII（ensure_ascii=True）：Windows 上 stdio pipe 透明用
    系统编码（cp936 等）转换 utf-8 字符串会破坏非 ASCII 字节，强制 ASCII
    编码无关，模型侧按 UTF-8 解析 JSON 字符串里 \\u 转义即可。
    """
    ctx = _make_context(root)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as exc:
                sys.stdout.write(
                    json.dumps(
                        _make_error(None, -32700, f"parse error: {exc}"),
                        ensure_ascii=True,
                    )
                    + "\n"
                )
                sys.stdout.flush()
                continue
            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params") or {}
            # 通知（无 id）不回
            if req_id is None and method != "initialize":
                continue
            try:
                response = _dispatch(ctx, method, params, req_id)
            except Exception as exc:  # 兜底：避免错误透传污染 stdio
                response = _make_error(req_id, -32603, f"internal error: {exc}")
            if req_id is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=True, default=str) + "\n")
                sys.stdout.flush()
    finally:
        conn = ctx.get("conn")
        if conn is not None:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    if _server_name() == "longtask-mcp":
        print(
            "warning: 'longtask-mcp' is deprecated; use 'lhgp-mcp' instead",
            file=sys.stderr,
        )

    parser = argparse.ArgumentParser(
        prog=_server_name(),
        description="LHGP Protocol MCP server (stdio JSON-RPC 2.0)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="LHGP 数据目录（默认 ~/.lhgp；旧安装可回退到 ~/.longtask）",
    )
    args = parser.parse_args(argv)
    root = Path(args.data_dir).expanduser().resolve() if args.data_dir else default_data_root()
    root.mkdir(parents=True, exist_ok=True)
    serve_stdio(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
