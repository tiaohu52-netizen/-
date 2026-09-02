"""全局 Kill Switch 文件判定（DESIGN §15.2）。

Kill Switch 是「熔断一切推进」的紧急开关：权威状态是数据根目录下的
KILL_SWITCH 标记文件。物理上是纯文件检查，无 I/O 依赖外的状态，
故放在 promoter 层供 tick 与 daemon 进程管理共用。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

KILL_SWITCH_FILE = "KILL_SWITCH"


def is_kill_switch_active(root: Path) -> bool:
    """检查全局 Kill Switch 是否处于激活状态（DESIGN §15.2）。"""
    return (root / KILL_SWITCH_FILE).is_file()


def set_kill_switch(root: Path, active: bool) -> None:
    """激活或解除全局 Kill Switch（DESIGN §15.2）。"""
    path = root / KILL_SWITCH_FILE
    if active:
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).isoformat()
        path.write_text(f"kill switch engaged at {ts}\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
