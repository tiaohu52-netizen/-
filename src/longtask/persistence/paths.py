"""与界面无关的持久化数据目录解析。"""

from __future__ import annotations

from pathlib import Path

LEGACY_DIR_NAME = ".longtask"
NEW_DIR_NAME = ".lhgp"


def default_data_root() -> Path:
    """优先使用 ``~/.lhgp``，未迁移的旧安装回退到 ``~/.longtask``。"""
    new = Path.home() / NEW_DIR_NAME
    legacy = Path.home() / LEGACY_DIR_NAME
    return new if new.exists() or not legacy.exists() else legacy


__all__ = ["default_data_root"]
