"""protocol/* 方法 handler：版本协商与事件流读取（DESIGN §11.2、§11.3）。

只有两个方法：protocol/hello（无副作用，回版本与方法表）和
protocol/events（按 cursor 分页读事件流）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any

from longtask import PROTOCOL_VERSION, __version__
from longtask.persistence.store import StoreError, get_events
from longtask.rpc.errors import ErrorCode, RpcError
from longtask.rpc.methods import Method

if TYPE_CHECKING:
    from longtask.rpc.server import RequestEnvelope


def handle_protocol_hello(
    envelope: RequestEnvelope,
    **kwargs: Any,
) -> dict[str, Any]:
    """服务端问候与版本协商（DESIGN §11.2）。"""
    return {
        "ok": True,
        "result": {
            "protocol_version": PROTOCOL_VERSION,
            "server_version": __version__,
            "methods": [m.value for m in Method],
        },
    }


def handle_protocol_events(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    **kwargs: Any,
) -> dict[str, Any]:
    """按 cursor 分页读取事件流（DESIGN §11.2、§11.3）。"""
    params = envelope.params
    contract_id: str | None = params.get("contract_id")

    cursor = _coerce_int(params.get("cursor", params.get("after_event_id")), "cursor")
    limit = _coerce_int(params.get("limit", 50), "limit")
    if limit is None or limit <= 0:
        raise RpcError(code=ErrorCode.VALIDATION_FAILED, message="limit must be positive")

    try:
        events = get_events(conn, contract_id=contract_id, after_event_id=cursor, limit=limit)
    except StoreError as exc:
        raise RpcError(code=ErrorCode.INTERNAL, message=str(exc)) from exc

    events_payload = [
        {
            "event_id": e.event_id,
            "contract_id": e.contract_id,
            "attempt_id": e.attempt_id,
            "lease_generation": e.lease_generation,
            "event_type": e.event_type,
            "payload_json": e.payload_json,
            "payload": json.loads(e.payload_json),
            "request_id": e.request_id,
            "created_at": e.created_at.isoformat(),
            "actor": e.actor,
            "schema_version": e.schema_version,
        }
        for e in events
    ]
    next_cursor = events[-1].event_id if events else cursor
    return {
        "ok": True,
        "result": {
            "events": events_payload,
            "next_cursor": next_cursor,
            "has_more": len(events) == limit,
        },
    }


def _coerce_int(raw: Any, name: str) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message=f"{name} must be an integer: {exc}",
        ) from exc


__all__ = ["handle_protocol_events", "handle_protocol_hello"]
