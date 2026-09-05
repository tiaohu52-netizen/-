"""dsh executor 包装器：为执行者尝试设置独立的 DSH_HOME。

与 dsh_verifier_wrap.py 对称：daemon 分离进程的环境无法在注册表
per-entry 覆盖，executor 尝试经本包装器固定 DSH_HOME=.dogfood/dsh-home
（minimax-cn/MiniMax-M2.7-highspeed 默认模型）。

用法（协议把 task_prompt 作为 argv 尾元素追加）：
  python dsh_executor_wrap.py <task_prompt>
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
        os.path.dirname(os.path.abspath(__file__)), "dsh-home"
    )
    venv_bin = Path(__file__).resolve().parents[2] / ".venv" / "Scripts"
    env["PATH"] = os.pathsep.join(
        [str(venv_bin), env.get("PATH", "")] if env.get("PATH") else [str(venv_bin)]
    )
    proc = subprocess.run(  # noqa: S603 —— 固定 argv，node 来自本机 CLI 安装
        ["node", DSH_BIN, "--profile", "headless", prompt],  # noqa: S607
        shell=False,
        env=env,
    )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
