#!/usr/bin/env python3
"""质量门主编排（CONTRIBUTING「质量门」）。

本地与 CI 同一命令：uv run python scripts/quality_gate.py
固定顺序，任一失败即停；fail-closed：工具缺失/环境不对一律报错退出，
绝不假装通过。快路径（pre-commit 增量钩子）的通过不代表本门通过。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Gate:
    label: str
    argv: tuple[str, ...]


def build_gates() -> list[Gate]:
    py = sys.executable
    return [
        # 使用门禁自身的解释器加载 Ruff，避免 uv/venv 已安装但 PATH 未暴露
        # 可执行文件时产生假阴性；模块缺失仍由 subprocess fail-closed。
        Gate("format", (py, "-m", "ruff", "format", "--check", "src", "tests", "scripts")),
        Gate("lint", (py, "-m", "ruff", "check", "src", "tests", "scripts")),
        Gate("arch", (py, "scripts/arch_check.py")),
        Gate("deps", (py, "scripts/deps_check.py")),
        Gate("claims", (py, "scripts/claims_check.py")),
        Gate("typecheck", (py, "-m", "mypy")),
        Gate(
            "test+coverage",
            (
                py,
                "-m",
                "pytest",
                "--cov=src/longtask",
                "--cov-report=term-missing",
                "--cov-fail-under=70",
            ),
        ),
    ]


def run_gate(gate: Gate) -> int:
    print(f"\n[gate] >>> {gate.label}", flush=True)
    executable = gate.argv[0]
    if executable != sys.executable and shutil.which(executable) is None:
        # fail-closed：工具缺失绝不跳过
        print(f"[gate] {gate.label}: tool '{executable}' not found; refusing to pass.", flush=True)
        return 1
    result = subprocess.run(gate.argv, cwd=REPO_ROOT, check=False)
    if result.returncode != 0:
        print(f"[gate] STOP after {gate.label} (exit {result.returncode})", flush=True)
        return result.returncode
    print(f"[gate] PASS {gate.label}", flush=True)
    return 0


def main() -> int:
    gates = build_gates()
    print(f"[gate] authoritative sequence ({len(gates)} gates), repo={REPO_ROOT}", flush=True)
    for gate in gates:
        status = run_gate(gate)
        if status != 0:
            return status
    print(f"\n[gate] ALL PASS ({len(gates)} gates)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
