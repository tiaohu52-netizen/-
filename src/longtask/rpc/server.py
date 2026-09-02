"""JSON-RPC 服务端骨架（DESIGN §11.1）。

v0.1 只保证本机单用户：Windows 命名管道 / Linux·macOS Unix domain socket，
TCP 不是默认传输。首次启动生成 endpoint token，客户端握手必带。
骨架范围：请求信封校验与路由表；传输层 Developer Preview 实现。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from longtask import PROTOCOL_VERSION
from longtask.cli.paths import default_data_root
from longtask.persistence.store import StoreConfig, connect, ensure_schema
from longtask.rpc.errors import ErrorCode, RpcError
from longtask.rpc.handlers import HANDLERS
from longtask.rpc.methods import Method

if TYPE_CHECKING:
    from longtask.adapters.registry import ExecutorRegistry


@dataclass(frozen=True, slots=True)
class RequestEnvelope:
    """请求信封（DESIGN §11.1）。

    request_id 在重试时保持不变，服务端对有副作用的方法按幂等键处理。
    """

    method: Method
    request_id: str
    client_id: str
    protocol_version: int
    params: dict[str, Any] = field(default_factory=dict)


def parse_envelope(raw: dict[str, Any]) -> RequestEnvelope:
    """信封解析与版本检查。fail-closed：任何字段缺失抛 RpcError。"""
    try:
        method = Method(str(raw["method"]))
        request_id = str(raw["request_id"])
        client_id = str(raw["client_id"])
        protocol_version = int(raw["protocol_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message=f"malformed request envelope: {exc}",
        ) from exc
    if protocol_version != PROTOCOL_VERSION:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message=(
                f"protocol_version {protocol_version} unsupported by this daemon "
                f"(speaks {PROTOCOL_VERSION})"
            ),
        )
    if not request_id or not client_id:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message="request_id and client_id must be non-empty",
        )
    return RequestEnvelope(
        method=method,
        request_id=request_id,
        client_id=client_id,
        protocol_version=protocol_version,
        params=dict(raw.get("params", {})),
    )


def route(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection | None = None,
    now: datetime | None = None,
    registry: ExecutorRegistry | None = None,
) -> dict[str, Any]:
    """方法路由与分发（DESIGN §11.1、§11.2）。

    分发至各具体 handler；支持显式注入持久层连接、执行器注册表与时间（用于测试确定性），
    若未提供 conn 则自动连接默认用户库 ~/.longtask/state.db。
    对暂未实现的方法抛出 STATE_FORBIDDEN。
    """
    handler = HANDLERS.get(envelope.method)
    if handler is None:
        raise RpcError(
            code=ErrorCode.STATE_FORBIDDEN,
            message=f"method '{envelope.method.value}' not implemented",
            details={"request_id": envelope.request_id},
        )

    current_time = now or datetime.now(UTC)
    if conn is not None:
        return handler(envelope, conn=conn, now=current_time, registry=registry)

    # 默认单用户库路径（DESIGN §3.1）
    default_db_dir = default_data_root()
    default_db_dir.mkdir(parents=True, exist_ok=True)
    default_conn = connect(StoreConfig(db_path=default_db_dir / "state.db"))
    try:
        ensure_schema(default_conn)
        return handler(envelope, conn=default_conn, now=current_time, registry=registry)
    finally:
        default_conn.close()
