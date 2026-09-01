"""执行器 manifest（DESIGN §12.4）。

manifest 是能力声明，不是能力证明：健康检查只能证明「现在能连接」；
适配器必须在每次 prepare 返回实际 enforcement，合同要求不满足就拒接。
权威定义在 schemas/executor-manifest.schema.json，本数据类是代码侧镜像。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from longtask.contracts.schema import Enforcement

MANIFEST_PROTOCOL_VERSION = 1


@dataclass(frozen=True, slots=True)
class SandboxCapability:
    """沙箱能力声明（DESIGN §12.4 capabilities.sandbox）。"""

    file_effects: str  # read-only | workspace-write | unsupported
    network: str  # deny | allow | unsupported
    process: str  # restricted | unsupported
    enforcement: Enforcement


@dataclass(frozen=True, slots=True)
class Capabilities:
    """能力声明字段表（DESIGN §12.4；与注册表条目 §8.1 同一字段表）。"""

    spawn: bool
    observe: bool
    cancel: bool
    notify: bool
    followup: bool
    steer: bool
    interrupt: bool
    context: str  # required | optional
    sandbox: SandboxCapability
    acceptance_evidence: bool


@dataclass(frozen=True, slots=True)
class ExecutorManifest:
    """执行器接入声明（DESIGN §12.4）。"""

    executor_id: str
    adapter_version: str
    transport: str  # bridge | subprocess
    capabilities: Capabilities
    limits: dict[str, int] = field(default_factory=dict)
    protocol_version: int = MANIFEST_PROTOCOL_VERSION
