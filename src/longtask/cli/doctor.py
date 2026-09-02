"""LHGP doctor 系统自检（DESIGN §15.2）。

自检项目：
1. Python 解释器大版本（>= 3.11）；
2. 存储目录 (~/.longtask) 读写权限；
3. SQLite 权威状态库 state.db 完整性与版本；
4. 执行器注册表 registry.json/yaml 解析与可用执行器数量；
5. 全局 Emergency Kill Switch 状态。
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from longtask import PROTOCOL_VERSION, __version__
from longtask.adapters.registry import ExecutorRegistry
from longtask.cli.paths import default_data_root
from longtask.persistence.store import STORE_SCHEMA_VERSION, StoreConfig, connect, ensure_schema


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    message: str
    details: str = ""


@dataclass(frozen=True, slots=True)
class DoctorReport:
    protocol_version: int
    package_version: str
    checks: tuple[CheckResult, ...] = ()

    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def format_text(self) -> str:
        lines: list[str] = [
            f"=== LHGP doctor (v{self.package_version}, protocol v{self.protocol_version}) ===",
        ]
        for c in self.checks:
            mark = "[PASS]" if c.ok else "[FAIL]"
            detail_str = f" ({c.details})" if c.details else ""
            lines.append(f"{mark:6s} {c.name}: {c.message}{detail_str}")
        lines.append("-----------------------------------------------------")
        summary = "ALL SYSTEMS GO" if self.all_ok else "DIAGNOSTIC ISSUES DETECTED"
        lines.append(f"Result: {summary}")
        return "\n".join(lines)


def run_doctor(root: Path | None = None) -> DoctorReport:
    """运行全套自检并产出报告（DESIGN §15.2）。"""
    data_dir = root or default_data_root()
    checks: list[CheckResult] = []

    # 1. 检查 Python 版本
    py_ok = sys.version_info >= (3, 11)
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks.append(
        CheckResult(
            name="python_runtime",
            ok=py_ok,
            message=f"Python {py_ver}" if py_ok else f"Python {py_ver} < 3.11 required",
        )
    )

    # 2. 检查数据目录读写
    dir_ok = True
    dir_msg = f"directory {data_dir} accessible"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".doctor_probe"
        test_file.write_text("probe", encoding="utf-8")
        test_file.unlink(missing_ok=True)
    except OSError as exc:
        dir_ok = False
        dir_msg = f"cannot write to {data_dir}: {exc}"
    checks.append(
        CheckResult(
            name="storage_directory",
            ok=dir_ok,
            message=dir_msg,
        )
    )

    # 3. 检查数据库连接与 schema 版本
    db_ok = True
    db_msg = "state.db healthy"
    db_path = data_dir / "state.db"
    try:
        conn = connect(StoreConfig(db_path=db_path))
        try:
            ensure_schema(conn)
            row = conn.execute("PRAGMA user_version").fetchone()
            cur_ver = int(row[0]) if row else 0
            if cur_ver != STORE_SCHEMA_VERSION:
                db_ok = False
                db_msg = f"schema version mismatch: got {cur_ver}, expected {STORE_SCHEMA_VERSION}"
        finally:
            conn.close()
    except (OSError, sqlite3.Error, Exception) as exc:
        db_ok = False
        db_msg = f"database error: {exc}"
    checks.append(
        CheckResult(
            name="database_integrity",
            ok=db_ok,
            message=db_msg,
        )
    )

    # 4. 检查执行器注册表
    reg_path = data_dir / "registry.json"
    reg_ok = True
    reg_msg = "registry accessible"
    reg_details = ""
    try:
        reg = ExecutorRegistry.load_from_file(reg_path)
        all_executors = reg.list_entries(enabled_only=False)
        enabled_executors = reg.list_entries(enabled_only=True)
        reg_details = f"{len(enabled_executors)} enabled / {len(all_executors)} registered"
    except Exception as exc:
        reg_ok = False
        reg_msg = f"cannot parse registry: {exc}"
    checks.append(
        CheckResult(
            name="executor_registry",
            ok=reg_ok,
            message=reg_msg,
            details=reg_details,
        )
    )

    # 5. 检查全局 Kill Switch 状态
    ks_path = data_dir / "KILL_SWITCH"
    ks_active = ks_path.is_file()
    ks_msg = "ACTIVE (emergency halt engaged)" if ks_active else "inactive (normal operation)"
    checks.append(
        CheckResult(
            name="kill_switch",
            ok=not ks_active,
            message=ks_msg,
        )
    )

    return DoctorReport(
        protocol_version=PROTOCOL_VERSION,
        package_version=__version__,
        checks=tuple(checks),
    )
