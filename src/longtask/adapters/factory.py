"""kind → 适配器实例的默认构造（DESIGN §8.1、§12）。

注册表条目只携带 launch 与能力声明；适配器实例由本工厂按 kind 构造，
调度层只经 ExecutorAdapter 公开协议使用，不感知具体实现（CONTRIBUTING 模块边界）。
未知 kind 返回 None，由分发侧按拒接处理（DESIGN §9 fail-closed，不猜、不降级）。
"""

from __future__ import annotations

from longtask.adapters.base import ExecutorAdapter
from longtask.adapters.fake_executor import FakeExecutor
from longtask.adapters.registry import RegistryEntry
from longtask.adapters.subprocess_adapter import SubprocessAdapter

__all__ = ["build_adapter"]


def build_adapter(entry: RegistryEntry) -> ExecutorAdapter | None:
    """按注册表条目构造适配器；未知 kind 返回 None（不猜、不降级）。"""
    if entry.kind == "subprocess":
        return SubprocessAdapter(manifest=entry.to_manifest(), launch=entry.launch)
    if entry.kind == "fake":
        return FakeExecutor()
    return None
