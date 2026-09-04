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
    proc = subprocess.run(  # noqa: S603 —— 固定 argv，prompt 是合同文本
        ["node", DSH_BIN, "--profile", "headless", prompt],
        shell=False,
        env=env,
    )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
