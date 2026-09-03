"""本机 JSON-RPC 客户端。

客户端与 :mod:`lhgp.rpc.server` 共享同一套协议版本、错误类型和请求
封装，旧的 ``longtask.rpc.client`` 路径仅作为兼容入口保留。
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from lhgp import PROTOCOL_VERSION
from lhgp.rpc.errors import ErrorCode, RpcError


def call_unix_socket(
    endpoint: Path,
    *,
    token: str,
    method: str,
    request_id: str,
    client_id: str,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """通过认证 Unix socket 调用 JSON-RPC 方法并返回响应。"""

    request = {
        "method": method,
        "request_id": request_id,
        "client_id": client_id,
        "protocol_version": PROTOCOL_VERSION,
        "params": params or {},
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:  # type: ignore[attr-defined]
        connection.settimeout(timeout)
        connection.connect(str(endpoint))
        connection.sendall((json.dumps({"token": token}) + "\n").encode("utf-8"))
        connection.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        reader = connection.makefile("r", encoding="utf-8", newline="\n")
        try:
            line = reader.readline()
        finally:
            reader.close()
    if not line:
        raise RpcError(
            code=ErrorCode.INTERNAL,
            message="RPC server closed connection without response",
        )
    response = json.loads(line)
    if not isinstance(response, dict):
        raise RpcError(code=ErrorCode.INTERNAL, message="RPC server returned a non-object response")
    if response.get("ok") is False:
        raise RpcError.from_payload(response)
    return response


__all__ = ["call_unix_socket"]
