"""kimi CLI 包装器：把 argv 尾元素（协议 task_prompt）转成 -p 的值。

协议 spawn 约定（DESIGN §12.1）：task_prompt 作为单个 argv 尾元素追加。
kimi CLI 的参数结构是 [options] [command]——prompt 必须是 -p 的直接值，
追加在尾部会被当成子命令名（unknown command '你是...'）。

本包装器：argv[:-1] 原样转发（flags），argv[-1] 作为 -p 的值。
Windows 编码陷阱全避开：subprocess list argv + shell=False，
stdout 直接继承（UTF-8 由 kimi 自管）。
"""

import subprocess
import sys

KIMI = r"C:\Users\17464\AppData\Roaming\npm\kimi.cmd"


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print("usage: kimi_wrap.py <flags...> <prompt>", file=sys.stderr)
        return 2
    prompt, flags = argv[-1], argv[:-1]
    cmd = [KIMI, *flags, "-p", prompt]
    proc = subprocess.run(  # noqa: S603 —— 固定 argv 前缀，prompt 是合同文本
        cmd,
        shell=False,
        cwd=None,
    )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
