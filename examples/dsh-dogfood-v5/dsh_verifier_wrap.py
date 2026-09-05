"""dsh verifier 包装器：为 verifier 尝试设置独立的 DSH_HOME。

协议 spawn 子进程继承 daemon 环境变量；executor 与 verifier 需要不同的
dsh 家目录（不同默认模型）时，注册表 argv 无法表达 per-entry 环境覆盖。
本包装器在子进程侧强制 DSH_HOME 指向 dsh-home-verifier，使「同 CLI、
不同模型」的独立性成为可部署形态。

用法（协议把 task_prompt 作为 argv 尾元素追加）：
  python dsh_verifier_wrap.py <task_prompt>
"""

import os
import subprocess
import sys
from pathlib import Path

DSH_BIN = (
    "C:\\Users\\17464\\AppData\\Roaming\\com.kimi.shell\\dsh\\current"
    "\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js"
)


def main() -> int:
    argv = sys.argv[1:]
    prompt = argv[-1] if argv else ""
    env = dict(os.environ)
    env["DSH_HOME"] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "dsh-home-verifier"
    )
    # The daemon intentionally exposes only its inherited PATH.  Make the
    # dogfood verifier's declared command checks reproducible by explicitly
    # placing this repository's isolated interpreter first; do not rely on a
    # developer shell having activated the venv.
    venv_bin = Path(__file__).resolve().parents[2] / ".venv" / "Scripts"
    env["PATH"] = os.pathsep.join(
        [str(venv_bin), env.get("PATH", "")] if env.get("PATH") else [str(venv_bin)]
    )
    proc = subprocess.run(  # noqa: S603, S607 —— 固定 argv，node 来自本机 CLI 安装
        ["node", DSH_BIN, "--profile", "headless", prompt],
        shell=False,
        env=env,
    )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
