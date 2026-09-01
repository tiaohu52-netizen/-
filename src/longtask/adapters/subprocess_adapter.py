"""通用 subprocess 适配器（DESIGN §9、§12.1、§15.2）。

适合只有 CLI、没有外部会话 API 的执行器。约束翻译遵循 §9 编译表：
编译失败的默认行为是拒接（PrepareRefusedError），绝不静默降级。
spawn 只收结构化 argv（列表参数、shell=False），模型输出是不可信数据，
永不进入命令行（DESIGN §12.1、§14 注入防线）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from longtask.adapters.base import (
    AttemptInput,
    ExecutorAdapter,
    PreparedLaunch,
    PrepareRefusedError,
)
from longtask.adapters.manifest import ExecutorManifest
from longtask.adapters.registry import LaunchSpec
from longtask.contracts.schema import AttemptState, Enforcement

# cancel 宽限期默认值：terminate 后等这么久仍存活才升级 kill（不真实长睡）。
DEFAULT_GRACE_SECONDS = 5.0
# collect 默认等待上限：超时抛 subprocess.TimeoutExpired（如实回收失败）。
DEFAULT_COLLECT_TIMEOUT_SECONDS = 60.0


class SubprocessAdapter(ExecutorAdapter):
    """结构化 argv 拉起的 CLI 执行器适配器。

    约束翻译（DESIGN §9 编译表，翻不了即拒接）：
    - file_effects.mode=workspace-write → 结构化 cwd 绑定，实测 enforcement=partial；
      read-only 等其余模式无强制面可证 → 拒接
    - workspace_root / deny_paths 服务端归一化后比较，deny 落入或覆盖
      workspace 即拒接（含 ../ 穿越变体与符号链接逃逸，DESIGN §14）
    - network.mode=deny 只有 manifest 声明独立网络策略（network=deny）才兑现；
      提示词不算（DESIGN §9）
    - process / package_install 无法证明 → 拒接
    """

    def __init__(
        self,
        manifest: ExecutorManifest,
        launch: LaunchSpec | None = None,
        grace_period_seconds: float = DEFAULT_GRACE_SECONDS,
        collect_timeout_seconds: float = DEFAULT_COLLECT_TIMEOUT_SECONDS,
    ) -> None:
        if grace_period_seconds <= 0:
            raise ValueError(f"grace_period_seconds 必须为正: {grace_period_seconds}")
        if collect_timeout_seconds <= 0:
            raise ValueError(f"collect_timeout_seconds 必须为正: {collect_timeout_seconds}")
        self._manifest = manifest
        self._launch = launch if launch is not None else LaunchSpec()
        self._grace_period_seconds = grace_period_seconds
        self._collect_timeout_seconds = collect_timeout_seconds
        # attempt_id → 进程映射：observe/cancel/collect 都以 attempt 为键
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._cancelled: set[str] = set()

    @property
    def id(self) -> str:
        return self._manifest.executor_id

    def describe(self) -> ExecutorManifest:
        return self._manifest

    def health(self) -> bool:
        """launch argv 干跑自检：可执行文件现在可达。

        只证明「现在能连接」，不证明任何能力（DESIGN §12.4）。
        """
        if not self._launch.argv:
            return False
        return shutil.which(self._launch.argv[0]) is not None

    def prepare(self, input_: AttemptInput) -> PreparedLaunch:
        """翻译合同硬约束为运行时强制面；翻不了 → PrepareRefusedError（拒接）。"""
        reasons: list[str] = []
        hard = _hard_constraints(input_, reasons)
        workspace = self._resolve_workspace(input_, hard, reasons)
        if workspace is not None:
            _check_deny_paths(hard, workspace, reasons)
        _check_file_effects(hard, self._manifest, reasons)
        _check_network(hard, self._manifest, reasons)
        _check_process(hard, reasons)
        _check_package_install(hard, reasons)
        _check_context(input_, reasons)
        if not self._launch.argv:
            reasons.append("launch argv 未配置：没有结构化 argv 可拉起")
        if reasons:
            raise refuse("; ".join(reasons))
        if workspace is None:
            # 不可达兜底：workspace 为 None 时 _resolve_workspace 必已记理由（fail-closed）
            raise refuse("workspace_root 解析失败")
        return PreparedLaunch(
            argv=self._launch.argv,
            cwd=str(workspace),
            env_allowlist=self._launch.env_allowlist,
            # 实测只有 cwd 绑定，报告 partial（DESIGN §12.4：夸口即拒接）
            enforcement=Enforcement.PARTIAL,
            context_snapshot_path=input_.context_snapshot_path,
        )

    def spawn(self, input_: AttemptInput, launch: PreparedLaunch) -> str:
        """按结构化声明拉起子进程（列表参数、shell=False），返回 session_ref。"""
        if input_.attempt_id in self._procs:
            raise ValueError(f"attempt 已拉起，拒绝重复 spawn: {input_.attempt_id}")
        reasons: list[str] = []
        if not launch.argv:
            reasons.append("launch.argv 为空：没有可执行的结构化声明")
        for element in launch.argv:
            if not isinstance(element, str) or not element:
                reasons.append("launch.argv 必须是非空字符串元组")
                break
        expected = self._resolve_workspace(input_, _hard_constraints(input_, []), reasons)
        if expected is None:
            reasons.append("spawn 无法确定期望 workspace_root，拒绝拉起")
        elif launch.cwd is not None and Path(launch.cwd).expanduser().resolve() != expected:
            # §14 适配器侧防线：伪造的 launch 不能借 spawn 把 cwd 挪出工作区
            reasons.append(f"launch.cwd 越出 workspace_root: {launch.cwd}")
        if reasons:
            raise refuse("; ".join(reasons))
        if expected is None:
            # 不可达兜底：expected 为 None 时上方必已记理由（fail-closed）
            raise refuse("spawn 无法确定期望 workspace_root")
        # 环境白名单：只透显式列出的变量；模型输出永不进入 argv 或环境
        env = {name: os.environ[name] for name in launch.env_allowlist if name in os.environ}
        # 任务文本作为单个 argv 尾元素（用户审定的冻结区数据，非模型输出；
        # 列表参数 + shell=False，无 shell 拼接面，DESIGN §12.1/§14 注入防线）
        argv = list(launch.argv)
        if input_.task_prompt is not None:
            if not input_.task_prompt.strip():
                raise refuse("task_prompt 为空白：没有可交付的任务文本")
            argv.append(input_.task_prompt)
        proc = subprocess.Popen(  # noqa: S603 —— 结构化 argv + shell=False，DESIGN §12.1 注入防线
            argv,
            cwd=launch.cwd if launch.cwd is not None else str(expected),
            shell=False,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._procs[input_.attempt_id] = proc
        return f"subprocess:{input_.attempt_id}:{proc.pid}"

    def observe(self, attempt_id: str) -> dict[str, object]:
        """观察进程状态：存活与退出码（骨架期返回结构由 Developer Preview 定稿）。"""
        proc = self._require(attempt_id)
        returncode = proc.poll()
        if returncode is None:
            state = AttemptState.RUNNING
        elif attempt_id in self._cancelled:
            state = AttemptState.CANCELLED
        elif returncode == 0:
            state = AttemptState.SUCCEEDED
        else:
            state = AttemptState.FAILED
        return {"state": state.value, "alive": returncode is None, "returncode": returncode}

    def cancel(self, attempt_id: str, reason: str) -> None:
        """取消 attempt：terminate 后有宽限期，仍存活才升级 kill。"""
        proc = self._require(attempt_id)
        if proc.poll() is not None:
            # 已自行退出：无可取消对象，保留原终态（不伪装成 cancelled）
            return
        proc.terminate()
        try:
            proc.wait(timeout=self._grace_period_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        self._cancelled.add(attempt_id)

    def collect(self, attempt_id: str) -> dict[str, object]:
        """回收结果：退出码 + stdout/stderr；未退出抛 TimeoutExpired（不伪造结果）。"""
        proc = self._require(attempt_id)
        stdout_bytes, stderr_bytes = proc.communicate(timeout=self._collect_timeout_seconds)
        returncode = proc.returncode
        if returncode is None:
            # communicate 返回即已退出；防御性兜底，不伪造结果
            raise RuntimeError(f"collect: 进程未退出: {attempt_id}")
        if attempt_id in self._cancelled:
            state = AttemptState.CANCELLED
        elif returncode == 0:
            state = AttemptState.SUCCEEDED
        else:
            state = AttemptState.FAILED
        return {
            "state": state.value,
            "returncode": returncode,
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        }

    def _require(self, attempt_id: str) -> subprocess.Popen[bytes]:
        """按 attempt 取进程；未知 attempt 用 KeyError 如实报告。"""
        try:
            return self._procs[attempt_id]
        except KeyError:
            raise KeyError(f"未知 attempt: {attempt_id}") from None

    def _resolve_workspace(
        self,
        input_: AttemptInput,
        hard: dict[str, Any],
        reasons: list[str],
    ) -> Path | None:
        """归一化 workspace_root（DESIGN §14：归一化后与 deny_paths 比较）。"""
        raw_candidates: list[str] = []
        file_effects = hard.get("file_effects")
        if isinstance(file_effects, dict):
            root = file_effects.get("workspace_root")
            if isinstance(root, str) and root.strip():
                raw_candidates.append(root)
        if input_.workspace_root.strip():
            raw_candidates.append(input_.workspace_root)
        if not raw_candidates:
            # 合同与 AttemptInput 都没给 workspace：退回注册表 launch.cwd
            if self._launch.cwd:
                raw_candidates.append(self._launch.cwd)
            else:
                reasons.append("workspace_root 缺失：无法绑定结构化 cwd")
                return None
        for raw in raw_candidates:
            # 相对路径会被 resolve() 静默拼进本进程 cwd，必须显式拒绝
            if not Path(raw).expanduser().is_absolute():
                reasons.append(f"workspace_root 必须是绝对路径: {raw}")
                return None
        resolved_candidates = [_normalize(raw) for raw in raw_candidates]
        first = resolved_candidates[0]
        if any(candidate != first for candidate in resolved_candidates[1:]):
            reasons.append(f"workspace_root 不一致（合同冻结区 vs AttemptInput）: {raw_candidates}")
            return None
        return first


def refuse(reason: str) -> PrepareRefusedError:
    """构造拒接异常。统一入口保证拒接理由总是人类可读（DESIGN §9）。"""
    return PrepareRefusedError(f"constraint untranslatable: {reason}")


def _hard_constraints(input_: AttemptInput, reasons: list[str]) -> dict[str, Any]:
    """取冻结区硬约束；形状不对按 fail-closed 记拒接理由。"""
    hard = input_.contract_snapshot.get("hard_constraints", {})
    if not isinstance(hard, dict):
        reasons.append("hard_constraints 必须是对象")
        return {}
    return hard


def _normalize(raw: str) -> Path:
    """服务端归一化：expanduser + resolve（跟随符号链接、折叠 ../）。"""
    return Path(raw).expanduser().resolve()


def _deny_base(raw: str) -> str:
    """剥掉 glob 尾巴得到禁写区根（如 ~/ref/** → ~/ref）。"""
    text = raw.strip()
    for suffix in ("/**/*", "/**/", "/**"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _check_file_effects(
    hard: dict[str, Any],
    manifest: ExecutorManifest,
    reasons: list[str],
) -> None:
    """file_effects.mode 翻译：本适配器只有 cwd 绑定能力（DESIGN §9）。"""
    file_effects = hard.get("file_effects")
    if not isinstance(file_effects, dict):
        return
    mode = file_effects.get("mode")
    if mode is None:
        return
    if mode != "workspace-write":
        reasons.append(f"file_effects.mode={mode!r} 无法翻译：本适配器只有 cwd 绑定能力")
        return
    if manifest.capabilities.sandbox.file_effects != "workspace-write":
        reasons.append(
            "manifest 声明的 file_effects 能力与 cwd 绑定不符: "
            f"{manifest.capabilities.sandbox.file_effects!r}"
        )


def _check_deny_paths(
    hard: dict[str, Any],
    workspace: Path,
    reasons: list[str],
) -> None:
    """deny_paths 前缀检查：禁写区不得落入或覆盖 workspace（含 ../ 穿越）。"""
    file_effects = hard.get("file_effects")
    if not isinstance(file_effects, dict):
        return
    deny_paths = file_effects.get("deny_paths")
    if deny_paths is None:
        return
    if not isinstance(deny_paths, list):
        reasons.append("deny_paths 必须是列表")
        return
    for raw in deny_paths:
        if not isinstance(raw, str) or not raw.strip():
            reasons.append("deny_paths 含非字符串或空条目")
            continue
        base = Path(_deny_base(raw)).expanduser()
        if not base.is_absolute():
            reasons.append(f"deny_paths 条目必须是绝对路径: {raw}")
            continue
        resolved = base.resolve()
        if resolved == workspace:
            reasons.append(f"deny_paths 禁写区与 workspace_root 重叠: {raw}")
        elif workspace in resolved.parents:
            # 归一化后仍在工作区内：直写子路径或 ../ 穿越变体都算（DESIGN §14）
            reasons.append(f"deny_paths 禁写区落在 workspace_root 内: {raw}")
        elif resolved in workspace.parents:
            reasons.append(f"deny_paths 禁写区覆盖 workspace_root: {raw}")


def _check_network(
    hard: dict[str, Any],
    manifest: ExecutorManifest,
    reasons: list[str],
) -> None:
    """network.mode=deny：只有 manifest 声明独立网络策略才兑现，提示词不算。"""
    network = hard.get("network")
    if not isinstance(network, dict):
        return
    if network.get("mode") == "deny" and manifest.capabilities.sandbox.network != "deny":
        reasons.append(
            "network.mode=deny 无法兑现：manifest 未声明独立网络策略"
            f"（声明的是 {manifest.capabilities.sandbox.network!r}），提示词不算"
        )


def _check_process(hard: dict[str, Any], reasons: list[str]) -> None:
    """process.mode=restricted：通用 subprocess 无进程沙箱，无法证明即拒接。"""
    process = hard.get("process")
    if isinstance(process, dict) and process.get("mode") == "restricted":
        reasons.append("process.mode=restricted 无法证明：通用 subprocess 无进程沙箱")


def _check_package_install(hard: dict[str, Any], reasons: list[str]) -> None:
    """package_install.mode=deny：无包管理拦截面，无法证明即拒接。"""
    package_install = hard.get("package_install")
    if isinstance(package_install, dict) and package_install.get("mode") == "deny":
        reasons.append("package_install.mode=deny 无法证明：无包管理拦截面")


def _check_context(input_: AttemptInput, reasons: list[str]) -> None:
    """context.required=true：必须携带已物化的 context_snapshot_path（DESIGN §9）。"""
    context = input_.contract_snapshot.get("context")
    if (
        isinstance(context, dict)
        and context.get("required") is True
        and not input_.context_snapshot_path
    ):
        reasons.append("context.required=true 但 AttemptInput 未携带 context_snapshot_path")


__all__ = [
    "DEFAULT_COLLECT_TIMEOUT_SECONDS",
    "DEFAULT_GRACE_SECONDS",
    "Enforcement",
    "LaunchSpec",
    "SubprocessAdapter",
    "refuse",
]
