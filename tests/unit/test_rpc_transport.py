# ruff: noqa: S106 - test fixture token is intentionally deterministic

from __future__ import annotations

import json

from longtask import PROTOCOL_VERSION
from longtask.rpc.errors import ErrorCode
from longtask.rpc.transport import process_lines


def request() -> str:
    return json.dumps(
        {
            "method": "attempt/status",
            "request_id": "r1",
            "client_id": "client",
            "protocol_version": PROTOCOL_VERSION,
            "params": {},
        }
    )


def test_token_hanagent-cliake_and_dispatch() -> None:
    seen: list[dict[str, object]] = []
    out = process_lines(
        [json.dumps({"token": "secret"}), request()],
        token="secret",
        dispatch=lambda raw: seen.append(raw) or {"ok": True, "result": {"ready": True}},
    )
    assert json.loads(out[0])["ok"] is True
    assert seen[0]["request_id"] == "r1"


def test_invalid_token_closes_without_dispatch() -> None:
    called = False

    def dispatch(_: dict[str, object]) -> dict[str, object]:
        nonlocal called
        called = True
        return {"ok": True}

    out = process_lines(
        [json.dumps({"token": "wrong"}), request()], token="secret", dispatch=dispatch
    )
    payload = json.loads(out[0])
    assert payload["error"]["code"] == ErrorCode.AUTH_FAILED.value
    assert called is False


def test_malformed_request_is_recoverable() -> None:
    out = process_lines(
        [json.dumps({"token": "secret"}), "{bad", request()],
        token="secret",
        dispatch=lambda _: {"ok": True},
    )
    assert json.loads(out[0])["error"]["code"] == ErrorCode.VALIDATION_FAILED.value
    assert json.loads(out[1])["ok"] is True
