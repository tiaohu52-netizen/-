"""MCP server 薄层（DESIGN §11.1、§17）：让任何 MCP 兼容的 agent harness 能
「发现并使用」longtask 协议。

设计选择：暴露 7 个**面向 AI 任务流**的工具（不是把 24 个 JSON-RPC 方法
一对一透传——那只是 RPC 隧道，不是 AI 接口）。模型用这几条工具就能
独立走完"立合同→批准→交付→验收"四步：

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

import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from longtask import PROTOCOL_VERSION, __version__
from longtask.adapters.registry import ExecutorRegistry
from longtask.cli.paths import default_data_root
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


# ─── MCP 工具：每个工具对应一个 JSON-RPC method + 入参映射 ─────────────


def tool_health(_args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "implementation_version": __version__,
        "status": "ok",
        "tools": TOOL_NAMES,
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
            "request_id": args.get("request_id", _now().isoformat()),
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
            "request_id": args.get("request_id", _now().isoformat()),
            "client_id": "mcp",
            "protocol_version": PROTOCOL_VERSION,
            "params": {
                "contract_id": args["contract_id"],
                "revision": args.get("revision"),
            },
        }
    )
    return route(envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"])


def tool_get_contract(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    envelope = parse_envelope(
        {
            "method": Method.CONTRACT_GET.value,
            "request_id": args.get("request_id", _now().isoformat()),
            "client_id": "mcp",
            "protocol_version": PROTOCOL_VERSION,
            "params": {"contract_id": args["contract_id"]},
        }
    )
    return route(envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"])


def tool_list_contracts(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    envelope = parse_envelope(
        {
            "method": Method.CONTRACT_LIST.value,
            "request_id": args.get("request_id", _now().isoformat()),
            "client_id": "mcp",
            "protocol_version": PROTOCOL_VERSION,
            "params": {
                "state": args.get("state"),
                "limit": args.get("limit", 20),
            },
        }
    )
    return route(envelope, conn=ctx["conn"], now=_now(), registry=ctx["registry"])


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
            "request_id": args.get("request_id", _now().isoformat()),
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
                    "request_id": args.get("request_id", _now().isoformat()),
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
                    "request_id": args.get("request_id", _now().isoformat()),
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
                },
            },
        },
    ),
    "longtask_get_contract": (
        tool_get_contract,
        {
            "description": "查询单份合同权威视图（§11.6 字段表）。",
            "inputSchema": {
                "type": "object",
                "required": ["contract_id"],
                "properties": {"contract_id": {"type": "string"}},
            },
        },
    ),
    "longtask_list_contracts": (
        tool_list_contracts,
        {
            "description": "按状态过滤列出合同。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "state": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
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
                },
            },
        },
    ),
}
TOOL_NAMES = sorted(TOOLS.keys())


# ─── JSON-RPC over stdio ────────────────────────────────────────────────────


def _make_response(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: Any, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _dispatch(
    ctx: dict[str, Any], method: str, params: dict[str, Any], req_id: Any
) -> dict[str, Any]:
    if method == "initialize":
        return _make_response(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "longtask-mcp", "version": __version__},
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
        tool_name = params.get("name")
        args = params.get("arguments") or {}
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

    parser = argparse.ArgumentParser(
        prog="longtask-mcp",
        description="LongTask Protocol MCP server (stdio JSON-RPC 2.0)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="LongTask 数据目录（默认 ~/.longtask）",
    )
    args = parser.parse_args(argv)
    root = Path(args.data_dir).expanduser().resolve() if args.data_dir else default_data_root()
    root.mkdir(parents=True, exist_ok=True)
    serve_stdio(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
