"""Canonical shared RPC handler guards.

Authentication and identifier validation live here so canonical handlers do not
need to import the legacy package.  Contract parsing and replay lookup remain
temporarily delegated until their persistence dependencies are migrated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lhgp.rpc.errors import ErrorCode, RpcError
from longtask.rpc.handlers._common import (
    _parse_iso,
    idempotent_replay,
    parse_contract_draft,
)

if TYPE_CHECKING:
    from lhgp.rpc.server import RequestEnvelope

_TRUSTED_CLIENT_ACTORS: dict[str, str] = {
    "longtask-cli": "user",
    "cli": "user",
    "cli-test": "user",
    "mcp": "model",
    "executor": "executor",
    "verifier": "verifier",
    "daemon": "daemon",
    "system": "system",
}


def resolve_actor(envelope: RequestEnvelope, params: dict[str, Any]) -> str:
    """从受信 client_id 派生 actor，拒绝未知客户端。"""
    actor = _TRUSTED_CLIENT_ACTORS.get(envelope.client_id)
    if actor is None:
        raise RpcError(
            code=ErrorCode.AUTH_FAILED,
            message=f"unknown client_id: {envelope.client_id}",
        )
    return actor


def require_contract_id(params: dict[str, Any]) -> str:
    """校验 contract_id 必填且 trim 后非空。"""
    contract_id = str(params.get("contract_id", "")).strip()
    if not contract_id:
        raise RpcError(code=ErrorCode.VALIDATION_FAILED, message="contract_id is required")
    return contract_id


__all__ = [
    "_parse_iso",
    "idempotent_replay",
    "parse_contract_draft",
    "require_contract_id",
    "resolve_actor",
]
