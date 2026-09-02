"""多 CLI × 多模型 真实考验（dogfood v4）。

用户决定（authority allowlist，虚假用户信息如实标注为演示数据）：
- 本任务注册 dsh-headless 与 kimi-code 两个 CLI 执行器，后端模型池
  覆盖 deepseek / minimax 两家；
- 合同 authority 显式框定：**dsh(minimax 后端) 只当 executor，
  kimi(deepseek 后端) 只当 verifier**；
- 考验点：
  1. default-deny：池里开着的其他组合（如 kimi 当 executor）不得被派；
  2. 真实分工：dsh 用 MiniMax-M2.7-highspeed 干活（写文件），
     kimi 用 deepseek-v4-pro 当独立 verifier 交叉核对交付物；
  3. 完整甜路径：executor succeeded → verifier pass → 合同 complete
     （不同 CLI、不同模型家族的交叉验收）。

用法：
    python dogfood_v4.py phase1   # 立合同（授权矩阵）+ 派工 dsh + 观察收尾
    python dogfood_v4.py phase2   # 派 verifier（kimi/deepseek）+ 轮询 + 裁决
    python dogfood_v4.py probe    # 只验证 default-deny（不真跑 LLM）
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))

# Windows consoles may still use a legacy code page; keep the evidence script
# runnable there without changing the protocol or the captured event format.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CID = "lt-dogfood-v04"

NOW = datetime(2026, 9, 2, 18, 0, 0, tzinfo=UTC)

TASK = (
    "在当前工作目录写一个 wordcount.py：实现 count_words(text: str) -> int，"
    "按空白符分词计数；再写 tests_wc.py 覆盖：'hello world' 为 2、"
    "'  a  b  ' 为 2、'' 为 0。用 'python tests_wc.py' 自测通过后结束。"
)


def make_draft():
    from longtask.contracts.authority import Authority, AuthorityBinding
    from longtask.contracts.schema import Acceptance, Budget, ContractDraft

    return ContractDraft(
        title="词数统计器（多 CLI×多模型考验）",
        objective=TASK,
        deadline_at=datetime.now(UTC) + timedelta(hours=2),
        hard_constraints={
            "file_effects": {
                "mode": "workspace-write",
                "workspace_root": str(ROOT / "ws"),
            }
        },
        acceptance=Acceptance(
            standard="tests_wc.py 全部断言通过且函数行为符合描述",
            checks=(
                "workspace 有 wordcount.py 且定义 count_words",
                "workspace 有 tests_wc.py 且覆盖三个用例",
                "python tests_wc.py 退出码 0",
            ),
        ),
        workload_initial_hours=2.0,
        budget=Budget(
            max_dispatches=4,
            max_escalations=2,
            max_concurrent_attempts=1,
            max_attempt_minutes=30,
            max_output_bytes=1048576,
            verification_attempts_reserved=2,
        ),
        # 用户决定（演示数据）：dsh 只当执行者（MiniMax 后端），kimi 只当
        # 验收者（kimi-k3 后端——与 MiniMax 不同模型家族，交叉验收）。
        # deepseek 后端在 dsh 侧实测可用（settings.yaml agent-default-model
        # 可切 x5anci/deepseek-v4-pro）；kimi 的 x5m 后端与 -p 一次性模式
        # 不兼容（400），故 kimi 用其原生 managed 后端。
        authority=Authority(
            executor_policy="explicit_allow",
            executors=(
                AuthorityBinding(
                    executor_id="dsh-headless",
                    models=("MiniMax-M2.7-highspeed",),
                    roles=("executor",),
                ),
                AuthorityBinding(
                    executor_id="kimi-code",
                    models=("kimi-k3",),
                    roles=("verifier",),
                ),
            ),
        ),
    )


def build_registry():
    import json

    dsh_bin = (
        r"C:\Users\17464\AppData\Roaming\com.kimi.shell\dsh\current"
        r"\node_modules\@deepseek-ai\dsh\lib\bin.js"
    )
    kimi_bin = r"C:\Users\17464\AppData\Roaming\npm\kimi.cmd"
    venv_py = r"D:\工作台\远期任务协议\.venv\Scripts\python.exe"
    env_dsh = [
        "DSH_HOME", "MINIMAX_CN_API_KEY", "PATH", "SYSTEMROOT", "SYSTEMDRIVE",
        "WINDIR", "COMSPEC", "TEMP", "TMP", "APPDATA", "USERPROFILE", "NODE_PATH",
    ]
    env_kimi = [
        "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP",
        "TMP", "APPDATA", "USERPROFILE", "NODE_PATH",
    ]

    def caps():
        return {
            "spawn": True, "observe": True, "cancel": True, "notify": False,
            "followup": False, "steer": False, "interrupt": True,
            "context": "optional",
            "sandbox": {
                "file_effects": "workspace-write", "network": "allow",
                "process": "unsupported", "enforcement": "partial",
            },
            "acceptance_evidence": True,
        }

    agents = [
        {
            "id": "dsh-headless", "kind": "subprocess",
            "launch": {
                "argv": ["node", dsh_bin, "--profile", "headless"],
                "cwd": None, "env_allowlist": env_dsh,
            },
            "capabilities": caps(),
            "limits": {"max_concurrent_attempts": 1},
            "cost_hint": "medium", "enabled": True,
        },
        {
            "id": "kimi-code", "kind": "subprocess",
            "launch": {
                # 包装器把 argv 尾元素（task_prompt）转成 -p 的值；默认
                # 后端 kimi-k3（-p 一次性模式与 x5m provider 不兼容，400）
                "argv": [venv_py, str(ROOT / "kimi_wrap.py")],
                "cwd": None, "env_allowlist": env_kimi,
            },
            "capabilities": caps(),
            "limits": {"max_concurrent_attempts": 1},
            "cost_hint": "high", "enabled": True,
        },
        # 干扰项：未获授权的第三执行器（验证 default-deny）
        {
            "id": "codex-cli", "kind": "subprocess",
            "launch": {
                "argv": ["codex", "exec"], "cwd": None,
                "env_allowlist": ["PATH"],
            },
            "capabilities": caps(),
            "limits": {"max_concurrent_attempts": 1},
            "cost_hint": "low", "enabled": True,
        },
    ]
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "registry.json").write_text(
        json.dumps({"agents": agents}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def setup_contract():
    from longtask.contracts.schema import ContractState
    from longtask.persistence.store import (
        StoreConfig, connect, ensure_schema, save_contract, update_contract_state,
    )

    conn = connect(StoreConfig(db_path=ROOT / "state.db"))
    try:
        ensure_schema(conn)
        save_contract(conn, make_draft(), contract_id=CID, now=NOW)
        update_contract_state(conn, contract_id=CID, new_state=ContractState.ACTIVE, now=NOW)
    finally:
        conn.close()


def _run_verifier_and_judge() -> None:
    """phase2：派 kimi/deepseek 当 verifier → 轮询 → 裁决。"""
    import time

    from longtask.adapters.registry import ExecutorRegistry
    from longtask.cli.runner import AttemptRunner
    from longtask.cli.tick import _judge_verifier_outcomes
    from longtask.persistence.store import StoreConfig, connect, get_contract

    conn = connect(StoreConfig(db_path=ROOT / "state.db"))
    try:
        registry = ExecutorRegistry.load_from_file(ROOT / "registry.json")
        runner = AttemptRunner(ROOT, conn, registry)
        view = get_contract(conn, CID)
        print(f"[phase2] contract state: {view.state}")
        # role=verifier 视角派工：只有 kimi-code 合法（authority roles）
        dispatched = runner._dispatch_verifier(
            datetime.now(UTC), contract_id=CID, executor_id="dsh-headless"
        )
        print(f"[phase2] verifier dispatched: {dispatched}")
        if not dispatched:
            print("[phase2] verifier 未派出——查 authority/事件")
            return
        for _ in range(120):  # 最多 10 分钟
            time.sleep(5)
            runner.poll_attempts(datetime.now(UTC))
            row = conn.execute(
                "SELECT state FROM attempts WHERE goal_id=? AND role='verifier' "
                "ORDER BY admitted_at DESC LIMIT 1",
                (CID,),
            ).fetchone()
            if row and row[0] in ("succeeded", "failed", "cancelled", "stale"):
                print(f"[phase2] verifier 终态: {row[0]}")
                break
        else:
            print("[phase2] verifier 未在窗口内完成")
            return
        _judge_verifier_outcomes(ROOT, conn, datetime.now(UTC))
        final = get_contract(conn, CID)
        print(f"[phase2] 裁决后合同状态: {final.state}")
    finally:
        conn.close()


def phase1() -> None:
    """立合同 + 派工（应派 dsh-headless，不派 kimi/codex）+ 轮询到 executor 终态。"""
    import time

    from longtask.adapters.registry import ExecutorRegistry
    from longtask.cli.runner import AttemptRunner
    from longtask.cli.tick import run_daemon_tick
    from longtask.persistence.store import StoreConfig, connect, get_contract

    build_registry()
    import shutil

    for victim in ("state.db", "contracts", "ws"):
        shutil.rmtree(ROOT / victim, ignore_errors=True)
        (ROOT / victim).unlink(missing_ok=True)
    (ROOT / "ws").mkdir(parents=True, exist_ok=True)
    setup_contract()

    conn = connect(StoreConfig(db_path=ROOT / "state.db"))
    try:
        registry = ExecutorRegistry.load_from_file(ROOT / "registry.json")
        res = run_daemon_tick(ROOT, conn, registry, now=datetime.now(UTC))
        started = res.get("attempts_started") or []
        print(f"[phase1] dispatched={res['dispatched']} started={started}")
        if not started:
            print("[phase1] FAIL: no dispatch")
            return
        # 考验点 1：executor 必须是 dsh-headless（authority roles 决定）
        assert started[0]["executor_id"] == "dsh-headless", (
            f"default-deny 失效? executor={started[0]['executor_id']}"
        )
        print("[phase1] ✓ executor=dsh-headless（kimi/codex 被 authority 排除）")
        runner = AttemptRunner(ROOT, conn, registry)
        ok = runner.start_attempt(datetime.now(UTC), **started[0])
        print(f"[phase1] dsh spawned: {ok}")
        # 轮询直到 executor 终态（dsh 干活一般 1-3 分钟）
        for _ in range(60):
            time.sleep(5)
            runner.poll_attempts(datetime.now(UTC))
            row = conn.execute(
                "SELECT state FROM attempts WHERE attempt_id=?",
                (started[0]["attempt_id"],),
            ).fetchone()
            if row and row[0] in ("succeeded", "failed", "cancelled", "stale"):
                print(f"[phase1] executor 终态: {row[0]}")
                break
        else:
            print("[phase1] executor 未在 5 分钟窗口完成（留给 phase2）")
        v = get_contract(conn, CID)
        print(f"[phase1] contract: {v.state}")
    finally:
        conn.close()


def probe() -> None:
    """考验点 1 单测：default-deny——不真跑 LLM，只验候选筛选。"""
    import shutil

    from longtask.adapters.registry import ExecutorRegistry
    from longtask.contracts.schema import ContractState
    from longtask.persistence.store import StoreConfig, connect, get_contract

    build_registry()
    for victim in ("state.db", "contracts", "ws"):
        shutil.rmtree(ROOT / victim, ignore_errors=True)
        (ROOT / victim).unlink(missing_ok=True)
    setup_contract()
    conn = connect(StoreConfig(db_path=ROOT / "state.db"))
    try:
        registry = ExecutorRegistry.load_from_file(ROOT / "registry.json")
        view = get_contract(conn, CID)
        exec_candidates = registry.match_candidates(view.draft, requested_role="executor")
        ver_candidates = registry.match_candidates(view.draft, requested_role="verifier")
        print("executor 视角候选:", [c.id for c in exec_candidates])
        print("verifier 视角候选:", [c.id for c in ver_candidates])
        assert [c.id for c in exec_candidates] == ["dsh-headless"], "executor 只该有 dsh"
        assert [c.id for c in ver_candidates] == ["kimi-code"], "verifier 只该有 kimi"
        print("[probe] ✓ default-deny：codex 全拒；kimi 不能当 executor；dsh 不能当 verifier")
    finally:
        conn.close()


if __name__ == "__main__":
    os.environ.setdefault("DSH_HOME", str(ROOT / "dsh-home"))
    phase = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if phase == "phase1":
        phase1()
    elif phase == "phase2":
        _run_verifier_and_judge()
    elif phase == "probe":
        probe()
    else:
        print("usage: dogfood_v4.py probe|phase1|phase2")
