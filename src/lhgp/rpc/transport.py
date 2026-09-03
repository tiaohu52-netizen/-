"""本机 JSON-RPC 线传输实现（DESIGN §11.1）。"""

from __future__ import annotations

import hmac
import json
import socket
import stat
import threading
from collections.abc import Callable, Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any, TextIO

from lhgp.rpc.errors import ErrorCode, RpcError
from lhgp.rpc.server import parse_envelope


def _error(code: ErrorCode, message: str) -> dict[str, Any]:
    return RpcError(code=code, message=message).to_payload()


def process_lines(
    lines: Iterable[str],
    *,
    token: str,
    dispatch: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[str]:
    """处理连接上的 JSON 行，首行为 token 握手，后续逐行返回响应。"""

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
    """在已建立的文本流上服务一条连接，每个响应立即 flush。"""

    for response in process_lines(reader, token=token, dispatch=dispatch):
        writer.write(response + "\n")
        writer.flush()


def serve_unix_socket(
    endpoint: Path,
    *,
    token: str,
    dispatch: Callable[[dict[str, Any]], dict[str, Any]],
    stop_event: threading.Event | None = None,
) -> None:
    """监听 Unix domain socket，逐连接服务 JSON-RPC。"""

    if not hasattr(socket, "AF_UNIX"):
        raise RuntimeError("this platform does not provide Unix domain sockets")
    endpoint = Path(endpoint)
    endpoint.parent.mkdir(parents=True, exist_ok=True)
    if endpoint.exists():
        if not stat.S_ISSOCK(endpoint.stat().st_mode):
            raise OSError(f"RPC endpoint exists and is not a socket: {endpoint}")
        endpoint.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(endpoint))
        with suppress(OSError):
            endpoint.chmod(0o600)
        server.listen(8)
        server.settimeout(0.5)
        while stop_event is None or not stop_event.is_set():
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            with connection:
                reader = connection.makefile("r", encoding="utf-8", newline="\n")
                writer = connection.makefile("w", encoding="utf-8", newline="\n")
                try:
                    serve_stream(reader, writer, token=token, dispatch=dispatch)
                finally:
                    reader.close()
                    writer.close()
    finally:
        server.close()
        with suppress(FileNotFoundError):
            endpoint.unlink()


__all__ = ["process_lines", "serve_stream", "serve_unix_socket"]
