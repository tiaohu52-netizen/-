"""Canonical JSON-RPC method vocabulary and idempotency set."""

from enum import StrEnum


class Method(StrEnum):
    PROTOCOL_HELLO = "protocol/hello"
    PROTOCOL_EVENTS = "protocol/events"
    CONTRACT_PREPARE = "contract/prepare"
    CONTRACT_APPROVE = "contract/approve"
    CONTRACT_GET = "contract/get"
    CONTRACT_LIST = "contract/list"
    CONTRACT_PATCH = "contract/patch"
    CONTRACT_PAUSE = "contract/pause"
    CONTRACT_RESUME = "contract/resume"
    CONTRACT_CANCEL = "contract/cancel"
    CONTRACT_ARBITRATE = "contract/arbitrate"
    ATTEMPT_STATUS = "attempt/status"
    ATTEMPT_LOGS = "attempt/logs"
    ATTEMPT_WRITE_BACK = "attempt/write-back"
    CONTEXT_REFRESH = "context/refresh"
    CONTEXT_PROMOTE = "context/promote"
    EXECUTOR_LIST = "executor/list"
    EXECUTOR_ENABLE = "executor/enable"
    EXECUTOR_DISABLE = "executor/disable"
    EXECUTOR_HEALTH = "executor/health"
    CONTROL_NOTIFY = "control/notify"
    CONTROL_FOLLOWUP = "control/followup"
    CONTROL_STEER = "control/steer"
    CONTROL_INTERRUPT = "control/interrupt"
    CONTROL_SPAWN = "control/spawn"
    LEASE_RENEW = "lease/renew"
    LEASE_RELEASE = "lease/release"
    GOAL_PREPARE = "goal/prepare"
    GOAL_ADMISSION_CHECK = "goal/admission-check"
    GOAL_GET = "goal/get"
    GOAL_LIST = "goal/list"
    GOAL_UPDATE = "goal/update"


IDEMPOTENT_METHODS = frozenset(
    {
        Method.CONTRACT_PREPARE,
        Method.CONTRACT_APPROVE,
        Method.CONTRACT_PATCH,
        Method.CONTRACT_PAUSE,
        Method.CONTRACT_RESUME,
        Method.CONTRACT_CANCEL,
        Method.CONTRACT_ARBITRATE,
        Method.GOAL_PREPARE,
        Method.GOAL_ADMISSION_CHECK,
        Method.CONTEXT_PROMOTE,
        Method.CONTROL_NOTIFY,
        Method.CONTROL_FOLLOWUP,
        Method.CONTROL_STEER,
        Method.CONTROL_INTERRUPT,
        Method.CONTROL_SPAWN,
        Method.LEASE_RENEW,
        Method.LEASE_RELEASE,
        Method.ATTEMPT_WRITE_BACK,
    }
)

__all__ = ["IDEMPOTENT_METHODS", "Method"]
