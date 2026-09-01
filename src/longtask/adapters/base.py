"""ExecutorAdapter 接口（DESIGN §12.1）。

语言无关的最小适配面。Python 参考实现用 Protocol 表达；
其他语言的适配器以 schemas/executor-manifest.schema.json +
JSON-RPC 控制面对齐同一语义。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from longtask.adapters.manifest import ExecutorManifest
    from longtask.contracts.schema import AttemptRole, Enforcement


@dataclass(frozen=True, slots=True)
class AttemptInput:
    """适配器 prepare/spawn 入参（DESIGN §11.6 字段表）。

    task_prompt 是任务文本（合同冻结区 objective 摘要，用户审定数据，
    非模型输出），由 spawn 作为单个 argv 尾元素传给 headless CLI
    （对齐 dsh --profile headless "task" 接口；shell=False 无注入面）。
    """

    attempt_id: str
    contract_id: str
    revision: int
    lease_generation: int
    role: AttemptRole
    contract_snapshot: dict[str, Any]  # 冻结区 + 验收条款快照
    handover_path: str
    workspace_root: str
    budget_remaining: dict[str, int]
    partition_id: str | None = None
    context_snapshot_path: str | None = None
    task_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedLaunch:
    """prepare 的成功产物：结构化启动声明 + 兑现证明（DESIGN §12.4）。

    spawn 只接受本对象携带的结构化 argv/cwd/env 白名单，
    不存在可拼接的 shell 字符串（DESIGN §12.1、§14.1 注入防线）。
    """

    argv: tuple[str, ...]
    cwd: str | None
    env_allowlist: tuple[str, ...]
    enforcement: Enforcement  # 适配器对合同硬约束的实测兑现等级
    context_snapshot_path: str | None = None


class PrepareRefusedError(Exception):
    """prepare 拒接（DESIGN §9：编译失败的默认行为，绝不静默降级）。

    对应线协议错误码 CONSTRAINT_UNTRANSLATABLE / CAPABILITY_MISSING。
    """


class ExecutorAdapter(Protocol):
    """执行器适配器协议（DESIGN §12.1）。

    实现者注意：prepare 失败必须抛 PrepareRefusedError（拒接），
    不得返回「降级版」PreparedLaunch。
    """

    @property
    def id(self) -> str:
        """执行器 id，与注册表条目一致（DESIGN §8.1）。"""
        ...

    def describe(self) -> ExecutorManifest:
        """版本化 manifest（DESIGN §12.4）。能力声明，不是能力证明。"""
        ...

    def health(self) -> bool:
        """健康检查：只证明「现在能连接」，不证明能力（DESIGN §12.4）。"""
        ...

    def prepare(self, input_: AttemptInput) -> PreparedLaunch:
        """翻译合同约束为运行时强制面；翻不了 → PrepareRefusedError（拒接）。"""
        ...

    def spawn(self, input_: AttemptInput, launch: PreparedLaunch) -> str:
        """拉起执行，返回 session_ref。只接收结构化启动声明。"""
        ...

    def observe(self, attempt_id: str) -> dict[str, Any]:
        """观察运行状态（骨架期返回结构由 Developer Preview 定稿）。"""
        ...

    def cancel(self, attempt_id: str, reason: str) -> None:
        """取消 attempt；结果必须如实回报 accepted/rejected/unsupported。"""
        ...

    def collect(self, attempt_id: str) -> dict[str, Any]:
        """回收结果：stdout/stderr、退出码、结构化进度、artifact 指针。"""
        ...
