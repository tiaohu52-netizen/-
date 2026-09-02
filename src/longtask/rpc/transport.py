"""本机 JSON-RPC 线传输（DESIGN §11.1）。

传输故意保持极小：客户端先发送一次 ``{"token": "..."}`` 握手，
随后每行一个请求信封，每行得到一个响应。真正的路由仍由
``longtask.rpc.server.route`` 负责，因此 stdio/MCP 与本机 socket 共用同一
协议语义。socket 监听由宿主进程决定；本模块只提供安全、可测试的连接循环。
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Iterable
from typing import Any, TextIO

from longtask.rpc.errors import ErrorCode, RpcError
from longtask.rpc.server import parse_envelope


def _error(code: ErrorCode, message: str) -> dict[str, Any]:
    return RpcError(code=code, message=message).to_payload()


def process_lines(
    lines: Iterable[str],
    *,
    token: str,
    dispatch: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[str]:
    """处理一条连接上的 JSON 行，返回序列化后的响应行。

    第一行必须是 token 握手；认证失败后立即终止连接，不向客户端透露路由
    细节。后续每一行都先做 JSON 对象校验和 envelope 版本校验，再调用 dispatch。
    单个请求错误不会污染连接，可继续处理后续请求。
    """

    iterator = iter(lines)
    try:
        raw_handshake = next(iterator)
    except StopIteration:
        return []
    try:
        handshake = json.loads(raw_handshake)
    except (TypeError, ValueError):
        return [json.dumps(_error(ErrorCode.AUTH_REQUIRED, "token handshake required"))]
    supplied = handshake.get("token") if isinstance(handshake, dict) else None
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, token):
        return [json.dumps(_error(ErrorCode.AUTH_FAILED, "invalid endpoint token"))]

    responses: list[str] = []
    for raw_line in iterator:
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            envelope = parse_envelope(request)
            response = dispatch(request)
            if not isinstance(response, dict):
                raise TypeError("dispatch must return a JSON object")
        except RpcError as exc:
            response = exc.to_payload()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            response = _error(ErrorCode.VALIDATION_FAILED, f"malformed JSON-RPC request: {exc}")
        except Exception as exc:  # transport must not tear down on handler bugs
            response = _error(ErrorCode.INTERNAL, f"internal dispatch error: {exc}")
        # 触发 parse_envelope 的校验并避免未使用变量被误解为绕过校验。
        _ = envelope if "envelope" in locals() else None
        responses.append(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    return responses


def serve_stream(
    reader: TextIO,
    writer: TextIO,
    *,
    token: str,
    dispatch: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    """在已建立的文本流上服务一条连接。每个响应立即 flush。"""

    for response in process_lines(reader, token=token, dispatch=dispatch):
        writer.write(response + "\n")
        writer.flush()


__all__ = ["process_lines", "serve_stream"]
