"""dogfood v5：Alpha 硬门槛 1-3 的真实目标跨断裂考验。

相对 v4（甜路径：单合同成功→验收→complete），v5 用一个真实的
多阶段 Goal 跨越三种断裂，验证 ROADMAP §1.1 Alpha 判据：

1. 断裂①（关闭会话）：executor attempt 的租约到期后，daemon 重派
   新 attempt 接力（不丢已证实的进度）。
2. 断裂②（切换 Agent CLI）：阶段 2 由不同 CLI（kimi）接力执行，
   阶段 1 的 dsh 执行者已消失。
3. 断裂③（重启 daemon）：halt_daemon → spawn_daemon，合同状态、
   阶段进度、事件流全部无损。
4. 修复闭环：阶段 2 至少经历一次 verifier fail → repair → reverify。

目标（真实工作，非玩具）：构建中文文本处理工具集
  stage-1 用 dsh/MiniMax 写工具 + 自测（wordcount 类）
  stage-2 用 kimi 接力写第二个工具 + 修复闭环
  stage-3 验证整合（daemon 重启发生在本阶段开始前）

用法：
  python examples/dsh-dogfood-v5/dogfood_v5.py setup          # 建 Goal + 阶段计划 + 绑定合同
  python examples/dsh-dogfood-v5/dogfood_v5.py build-registry # 重建本地执行器注册表
  python examples/dsh-dogfood-v5/dogfood_v5.py stage1         # 真实 daemon + 断裂①注入
  python examples/dsh-dogfood-v5/dogfood_v5.py stage1-verify  # 验收 stage-1
  python examples/dsh-dogfood-v5/dogfood_v5.py stage2         # kimi CLI 接力 + 独立 verifier
  python examples/dsh-dogfood-v5/dogfood_v5.py stage3         # 重启 daemon 后继续 stage-3
  python examples/dsh-dogfood-v5/dogfood_v5.py status         # 查看当前状态
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# dogfood_v5.py 位于 <repo>/examples/dsh-dogfood-v5/；所有状态与包装器
# 路径必须锚定仓库根，而不是 examples/（否则会打开错误的 state.db）。
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from longtask.cli.daemon import get_daemon_status, halt_daemon, spawn_daemon  # noqa: E402
from longtask.persistence.events_query import get_events  # noqa: E402
from longtask.persistence.store import (  # noqa: E402
    StoreConfig,
    connect,
    ensure_schema,
    get_goal,
)
from longtask.rpc.handlers.goal import (  # noqa: E402
    handle_goal_prepare,
    handle_goal_update,
)
from longtask.rpc.methods import Method  # noqa: E402
from longtask.rpc.server import RequestEnvelope  # noqa: E402

ROOT = REPO / ".dogfood-v5"
WS = ROOT / "ws"

GOAL_ID = "lt-dogfood-v05"
DSH_BIN = (
    "C:\\Users\\17464\\AppData\\Roaming\\com.kimi.shell\\dsh\\current"
    "\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js"
)

STAGES = [
    {
        "id": "stage-1",
        "title": "dsh/MiniMax 实现字符频率工具",
        # target 相对 workspace_root 解析（dogfood 发现 6：模型不知道该
        # 约定，写了 ws/ 前缀导致 check 找 ws/ws/charfreq.py 不存在）
        "acceptance_checks": [
            "file-exists:charfreq.py",
            "command-exit-zero:python test_charfreq.py",
        ],
    },
    {
        "id": "stage-2",
        "title": "切换执行器接力实现行数统计工具（跨 CLI 接力 + 修复闭环）",
        "acceptance_checks": [
            "file-exists:linecount.py",
            "command-exit-zero:python test_linecount.py",
        ],
    },
    {
        "id": "stage-3",
        "title": "daemon 重启后整合验证",
        "acceptance_checks": [
            "file-exists:README_tools.md",
        ],
    },
]


def _now() -> datetime:
    return datetime.now(UTC)


def _conn():
    c = connect(StoreConfig(db_path=ROOT / "state.db"))
    ensure_schema(c)
    return c


def _envelope(method: Method, request_id: str, params: dict) -> RequestEnvelope:
    return RequestEnvelope(
        method=method,
        request_id=request_id,
        client_id="mcp",
        protocol_version=2,
        params=params,
    )


def _dsh_entry() -> dict[str, Any]:
    """dsh-headless 执行器：经包装器固定 DSH_HOME=dsh-home（minimax-cn）。

    dogfood 发现 3（环境）：用户主目录 ~/.dsh 的默认 provider 会
    Request timed out；.dogfood/dsh-home（MINIMAX_CN_API_KEY）实测可通。
    daemon 是分离进程，注册表 argv 无法表达 per-entry 环境覆盖，
    由 dsh_executor_wrap.py 在子进程侧强制。
    """
    return {
        "id": "dsh-headless",
        "kind": "subprocess",
        "launch": {
            "argv": [str(REPO / ".venv" / "Scripts" / "python.exe"),
                     str(REPO / ".dogfood" / "dsh_executor_wrap.py")],
            "cwd": None,
            "env_allowlist": [
                "DSH_HOME", "MINIMAX_CN_API_KEY", "PATH", "SYSTEMROOT",
                "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
                "APPDATA", "USERPROFILE", "NODE_PATH",
            ],
        },
        "capabilities": {
            "spawn": True, "observe": True, "cancel": True, "notify": False,
            "followup": False, "steer": False, "interrupt": True,
            "context": "optional",
            "sandbox": {
                "file_effects": "workspace-write", "network": "allow",
                "process": "unsupported", "enforcement": "partial",
            },
            "acceptance_evidence": True,
        },
        "limits": {"max_concurrent_attempts": 1},
        "cost_hint": "medium",
        "enabled": True,
    }


def _kimi_entry() -> dict[str, Any]:
    """verifier 执行器：dsh CLI + deepseek-v4-pro（不同模型家族，§5.2）。

    dogfood 发现 4（环境）：kimi CLI 的 provider 当日完全不可用（挂起至
    timeout）；改为「同 CLI 不同 DSH_HOME/模型」的独立 verifier——dsh-
    home-verifier 的 agent-default-model 是 x5m1/deepseek-v4-pro-0813，
    与执行者的 minimax-cn/MiniMax-M2.7-highspeed 不同家族。
    独立性降级说明：同 CLI 不同 DSH_HOME/模型，弱于 v4 的「不同 CLI」
    （kimi/kimi-k3）；§5.2 的不同 attempt/不同模型家族两条件仍满足。
    """
    os.environ.setdefault("LHGP_DSH_VERIFIER_HOME", str(REPO / ".dogfood" / "dsh-home-verifier"))
    return {
        "id": "dsh-verifier",
        "kind": "subprocess",
        "launch": {
            # 包装器在子进程侧强制 DSH_HOME=dsh-home-verifier（deepseek 默认
            # 模型）；协议 spawn 环境继承无法表达 per-entry 环境覆盖
            "argv": [str(REPO / ".venv" / "Scripts" / "python.exe"),
                     str(REPO / ".dogfood" / "dsh_verifier_wrap.py")],
            "cwd": None,
            "env_allowlist": [
                "DSH_HOME", "MINIMAX_CN_API_KEY", "X5M1_API_KEY", "PATH",
                "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP",
                "TMP", "APPDATA", "USERPROFILE", "NODE_PATH",
            ],
        },
        "capabilities": {
            "spawn": True, "observe": True, "cancel": True, "notify": False,
            "followup": False, "steer": False, "interrupt": True,
            "context": "optional",
            "sandbox": {
                "file_effects": "workspace-write", "network": "allow",
                "process": "unsupported", "enforcement": "partial",
            },
            "acceptance_evidence": True,
        },
        "limits": {"max_concurrent_attempts": 1},
        "cost_hint": "high",
        "enabled": True,
    }


def _kimi_executor_entry() -> dict[str, Any]:
    """kimi CLI 接力执行器（仅在 stage-2 实测时启用）。"""
    return {
        "id": "kimi-code",
        "kind": "subprocess",
        "launch": {
            "argv": [
                str(REPO / ".venv" / "Scripts" / "python.exe"),
                str(REPO / "examples" / "dsh-dogfood-v4" / "kimi_wrap.py"),
            ],
            "cwd": None,
            "env_allowlist": [
                "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
                "TEMP", "TMP", "APPDATA", "USERPROFILE", "NODE_PATH",
            ],
        },
        "capabilities": {
            "spawn": True, "observe": True, "cancel": True, "notify": False,
            "followup": False, "steer": False, "interrupt": True,
            "context": "optional",
            "sandbox": {
                "file_effects": "workspace-write", "network": "allow",
                "process": "unsupported", "enforcement": "partial",
            },
            "acceptance_evidence": True,
        },
        "limits": {"max_concurrent_attempts": 1},
        "cost_hint": "high",
        "enabled": True,
    }


def build_registry() -> None:
    """注册 dsh（executor 主力）与 kimi（接力 + verifier），直接写 JSON。

    registry.json 是注册表配置数据（协议零硬编码纪律）；load_from_file
    负责解析成 RegistryEntry 对象。
    """
    ROOT.mkdir(parents=True, exist_ok=True)
    WS.mkdir(parents=True, exist_ok=True)
    agents = [_dsh_entry(), _kimi_entry(), _kimi_executor_entry()]
    (ROOT / "registry.json").write_text(
        json.dumps({"agents": agents}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[v5] registry -> {ROOT / 'registry.json'} (dsh-headless, dsh-verifier, kimi-code)")


def _stage_draft(
    stage: dict,
    *,
    executor_id: str | None = None,
    executor_model: str = "*",
    verifier_id: str | None = None,
    verifier_model: str = "*",
) -> dict:
    """从阶段生成合同草案（阶段模板 → 草案的雏形）。"""
    draft = {
        "title": stage["title"],
        "objective": (
            f"在 {WS} 下完成该阶段交付物并通过验收 checks（target 相对"
            f" {WS} 解析）：" + "；".join(stage["acceptance_checks"])
        ),
        "deadline_at": _stage_deadline(),
        "hard_constraints": {
            "file_effects": {"mode": "workspace-write", "workspace_root": str(WS)}
        },
        "acceptance": {
            "standard": "全部 checks 通过",
            "checks": [
                {"kind": "file-exists", "target": c.split(":", 1)[1]}
                if c.startswith("file-exists:")
                else {"kind": "command-exit-zero", "target": c.split(":", 1)[1]}
                for c in stage["acceptance_checks"]
            ],
            "verifier": "cross_check",
        },
        "workload_estimate": {"initial_hours": 1.5},
        "budget": {
            "max_dispatches": 5, "max_escalations": 1,
            "max_concurrent_attempts": 1, "max_attempt_minutes": 30,
            "max_output_bytes": 1048576,
        },
    }
    if executor_id or verifier_id:
        bindings = []
        if executor_id:
            bindings.append(
                {"executor_id": executor_id, "models": [executor_model], "roles": ["executor"]}
            )
        if verifier_id:
            bindings.append(
                {"executor_id": verifier_id, "models": [verifier_model], "roles": ["verifier"]}
            )
        draft["authority"] = {"executor_policy": "explicit_allow", "executors": bindings}
    return draft


def _stage_deadline() -> str:
    """deadline 1h < workload 1.5h → u=1.5 → RESPAWN 档立即派工。

    真实场景里这是「排晚了」的合同；dogfood 里用来让 tick 立刻推进，
    不用等 QUEUED 的 1h 复核周期。
    """
    return (_now() + timedelta(hours=1)).isoformat()


def setup() -> None:
    """建 Goal + 阶段计划；阶段 1 立绑定合同。

    goal/prepare 带 stage_id 要求 Goal 已存在（§4.3 绑定不变式），而 Goal
    由首次合同落库创建——所以先不带 stage 起草同一份合同（创建 Goal），
    写入阶段计划后再带 stage_id 重新 prepare：同一 contract_id 幂等重放
    走事件反查路径，改用显式绑定校验。这里直接分两步：先立合同建 Goal，
    再 update plan，最后用 patch_goal 写回 stage-1 绑定。
    """
    build_registry()
    conn = _conn()
    now = _now()
    # 第一步：无 stage 起草 → 创建 Goal + 合同
    result = handle_goal_prepare(
        _envelope(Method.GOAL_PREPARE, "req-v5-setup", {
            "contract_id": GOAL_ID,
            "goal_id": GOAL_ID,
            "draft": _stage_draft(STAGES[0]),
        }),
        conn=conn, now=now,
    )
    assert result["ok"], result
    # 第二步：写入完整阶段计划
    goal = get_goal(conn, GOAL_ID)
    assert goal is not None
    plan = {"stages": [dict(s) for s in STAGES]}
    for stage in plan["stages"]:
        if stage["id"] == "stage-1":
            stage["contract_id"] = GOAL_ID
    handle_goal_update(
        _envelope(Method.GOAL_UPDATE, "req-v5-plan", {
            "goal_id": GOAL_ID, "revision": goal["revision"], "plan": plan,
        }),
        conn=conn, now=now,
    )
    verified = get_goal(conn, GOAL_ID)
    assert verified is not None
    bound = verified["plan"]["stages"][0].get("contract_id")
    assert bound == GOAL_ID, f"stage-1 binding lost: {bound}"
    print(f"[v5] goal {GOAL_ID}: 3 stages planned, stage-1 bound to {bound}")
    conn.close()


def status() -> None:
    conn = _conn()
    goal = get_goal(conn, GOAL_ID)
    if goal is None:
        print("[v5] goal not found; run setup first")
        conn.close()
        return
    print("=== Goal ===")
    print(f"revision={goal['revision']} progress={json.dumps(goal['progress'], ensure_ascii=False)}")
    for s in goal["plan"]["stages"]:
        print(f"  {s['id']}: bound={s.get('contract_id')}")
    print("=== Contracts ===")
    for row in conn.execute("SELECT contract_id, state FROM contracts").fetchall():
        print(f"  {row[0]}: {row[1]}")
    print("=== Daemon ===")
    print(json.dumps(get_daemon_status(ROOT), ensure_ascii=False))
    print("=== Events (last 10) ===")
    events = get_events(conn)
    for e in events[-10:]:
        print(f"  {e.created_at} {e.event_type} actor={e.actor}")
    conn.close()


def _wait_executor_terminal(conn, timeout_s: int = 600) -> str | None:
    """轮询最近 executor attempt 到终态。"""
    import time

    for _ in range(timeout_s // 5):
        time.sleep(5)
        row = conn.execute(
            "SELECT state FROM attempts WHERE goal_id=? AND role='executor' "
            "ORDER BY admitted_at DESC LIMIT 1",
            (GOAL_ID,),
        ).fetchone()
        if row and row[0] in ("succeeded", "failed", "cancelled", "stale", "orphaned"):
            return str(row[0])
    return None


def _require_current_stage(goal: dict[str, Any], stage_id: str) -> None:
    """拒绝跳阶段运行，避免 dogfood 产生不可解释的伪证据。"""
    current = goal.get("progress", {}).get("current")
    if current != stage_id:
        raise RuntimeError(
            f"expected current stage {stage_id!r}, got {current!r}; "
            "complete the previous stage and re-run status first"
        )


def _executor_pids() -> list[int]:
    """dsh 执行进程的真实 PID（按命令行匹配 dsh bin.js）。"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"name='node.exe'\" | "
         "Where-Object {$_.CommandLine -like '*dsh*bin.js*'} | "
         "Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=30,
    )
    return [int(line) for line in out.stdout.split() if line.strip().isdigit()]


def _kill_session(pids: list[int]) -> None:
    """断裂①：硬杀执行进程 = 原会话死亡（无优雅退出、无 write-back）。"""
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=15)
        print(f"[v5] BREAK-1 injected: killed executor pid {pid}")


def run_stage1() -> None:
    """阶段 1：批准 → 起真实 daemon → dsh 执行 → 断裂①（杀会话）→ 重派。

    真实形态（区别于 v4 的脚本内 tick）：daemon 是 spawn_daemon 分离出的
    真实常驻进程，每秒 tick；「关闭原会话」用 taskkill 硬杀 dsh 进程模拟
    （协议视角 = 进程消失 + 心跳停止）。租约到期后 daemon 走回收重派，
    新 attempt 从 handover/active.md 接力。
    """
    import time

    from longtask.contracts.schema import ContractState
    from longtask.persistence.store import update_contract_state

    conn = _conn()
    update_contract_state(conn, contract_id=GOAL_ID, new_state=ContractState.ACTIVE, now=_now())
    print("[v5][stage-1] contract approved -> ACTIVE")
    conn.close()

    res = spawn_daemon(ROOT, interval_seconds=2.0)
    if not res.get("ok"):
        print(f"[v5] daemon spawn failed: {res}")
        return
    print(f"[v5] daemon spawned: pid={res['pid']} interval={res['interval_seconds']}s")

    # 等 dsh 执行进程出现
    victim_pids: list[int] = []
    for _ in range(60):
        time.sleep(5)
        victim_pids = _executor_pids()
        if victim_pids:
            print(f"[v5][stage-1] executor up: pids={victim_pids}")
            break
    if not victim_pids:
        print("[v5][stage-1] WARN: executor process never appeared in 5min")
        return

    # 给它 60s 真实干活（写部分进度），然后注入断裂
    time.sleep(60)
    still = _executor_pids()
    if still:
        _kill_session(still)
    else:
        print("[v5][stage-1] executor exited before injection (may have finished)")

    # 轮询 daemon 的恢复行为：租约到期 → 回收 → 重派 → 新 attempt 接力
    conn = _conn()
    for i in range(90):
        time.sleep(10)
        rows = conn.execute(
            "SELECT attempt_id, state FROM attempts WHERE goal_id=? AND role='executor' "
            "ORDER BY admitted_at",
            (GOAL_ID,),
        ).fetchall()
        contract_state = conn.execute(
            "SELECT state FROM contracts WHERE contract_id=?", (GOAL_ID,)
        ).fetchone()[0]
        if i % 6 == 0:
            print(f"[v5][stage-1] t+{(i+1)*10}s attempts={rows} contract={contract_state}")
        if contract_state == "complete":
            print(f"[v5][stage-1] CONTRACT COMPLETE (via verifier): {rows}")
            break
        if any(r[1] == "succeeded" for r in rows) and contract_state == "active":
            # executor 成功了，等 verifier 裁决
            pass
    conn.close()


def run_stage1_verify() -> None:
    """stage-1 收尾：为 stage-1 立修订合同 → verifier 裁决 → 阶段推进。

    dogfood 发现链（如实记录）：
    1.（外部）dsh LLM API 超时 ×5 → 预算耗尽 blocked(need-user)。
       协议按退出码如实记失败——正确。
    2.（协议缺口）执行者已产出有效交付物 + 预算耗尽时，用户没有
       「直接请求验收」的命令面。
    3.（协议张力）verifier 上次因外部超时失败，verification_
       attempts_reserved=1 已耗尽——验证预算独立记账（§12.4）本身
       正确，但 reserved=1 + 外部抖动 = 一轮都重试不了。
    解法（协议忠实路径）：为 stage-1 立修订合同（同 stage 新合同 v2、
    新预算），旧 blocked 合同留作历史。交付物已在工作区，新合同的
    verifier 直接核对现有交付物。
    """
    import time

    from longtask.adapters.registry import ExecutorRegistry
    from longtask.cli.runner import AttemptRunner
    from longtask.cli.tick import _judge_verifier_outcomes
    from longtask.contracts.schema import ContractState
    from longtask.persistence.store import update_contract_state

    conn = _conn()
    now = _now()
    # 解绑旧 blocked 合同（stage 绑定不变式：一个阶段一份合同）。
    # 旧合同留在 goal.contract_ids 历史里；blocked(need-user) 态如实保留。
    goal = get_goal(conn, GOAL_ID)
    assert goal is not None
    plan = dict(goal["plan"])
    stages = [dict(s) for s in plan["stages"]]
    for s in stages:
        if s["id"] == "stage-1":
            s["contract_id"] = None
    plan["stages"] = stages
    handle_goal_update(
        _envelope(Method.GOAL_UPDATE, f"req-v5-s1unbind-{now.strftime('%H%M%S')}", {
            "goal_id": GOAL_ID, "revision": goal["revision"], "plan": plan,
        }),
        conn=conn, now=now,
    )
    print("[v5][stage-1v] unbound blocked contract from stage-1")

    # 修订合同：同 stage、新 contract_id、reserved 提到 3
    draft = _stage_draft(STAGES[0])
    draft["budget"]["verification_attempts_reserved"] = 3
    result = handle_goal_prepare(
        _envelope(Method.GOAL_PREPARE, f"req-v5-s1v2-{now.strftime('%H%M%S')}", {
            "contract_id": "lt-20260904-s1v2",
            "goal_id": GOAL_ID,
            "stage_id": "stage-1",
            "draft": draft,
        }),
        conn=conn, now=now,
    )
    assert result["ok"], result
    update_contract_state(conn, contract_id="lt-20260904-s1v2",
                          new_state=ContractState.ACTIVE, now=now)
    print("[v5][stage-1v] revision contract ACTIVE: lt-20260904-s1v2")

    registry = ExecutorRegistry.load_from_file(ROOT / "registry.json")
    runner = AttemptRunner(ROOT, conn, registry)
    # 直调 verifier 派生（dsh-verifier ≠ dsh-headless 执行者；deepseek 模型）
    ok = runner._dispatch_verifier(  # noqa: SLF001 —— dogfood：模拟用户触发验收
        now, contract_id="lt-20260904-s1v2", executor_id="dsh-headless"
    )
    print(f"[v5][stage-1v] verifier dispatched: {ok}")
    if not ok:
        for r in conn.execute(
            "SELECT event_type, substr(payload_json,1,200) FROM events "
            "WHERE event_type LIKE 'escalation%' ORDER BY event_id DESC LIMIT 2"
        ).fetchall():
            print("  ", r)
        conn.close()
        return

    for i in range(120):
        time.sleep(10)
        runner.poll_attempts(_now())
        row = conn.execute(
            "SELECT state, acceptance_status FROM contracts WHERE contract_id='lt-20260904-s1v2'"
        ).fetchone()
        ver = conn.execute(
            "SELECT state FROM attempts WHERE role='verifier' ORDER BY admitted_at DESC LIMIT 1"
        ).fetchone()
        if i % 6 == 0:
            print(f"[v5][stage-1v] t+{(i+1)*10}s contract={row} verifier={ver}")
        if ver and ver[0] in ("succeeded", "failed"):
            _judge_verifier_outcomes(ROOT, conn, _now())
            final = conn.execute(
                "SELECT state, acceptance_status FROM contracts WHERE contract_id='lt-20260904-s1v2'"
            ).fetchone()
            print(f"[v5][stage-1v] verifier {ver[0]} -> judged: {final}")
            break
    goal = get_goal(conn, GOAL_ID)
    if goal:
        print(f"[v5][stage-1v] goal progress: {json.dumps(goal['progress'], ensure_ascii=False)}")
        for s in goal["plan"]["stages"]:
            print(f"  {s['id']}: bound={s.get('contract_id')}")
    conn.close()


def run_stage2() -> None:
    """阶段 2：以 kimi CLI 接力，证明 executor 可在 Goal 内替换。"""
    import time

    from longtask.contracts.schema import ContractState
    from longtask.persistence.store import update_contract_state

    contract_id = f"{GOAL_ID}-stage2"
    conn = _conn()
    try:
        goal = get_goal(conn, GOAL_ID)
        if goal is None:
            raise RuntimeError("goal not found; run setup first")
        _require_current_stage(goal, "stage-2")
        draft = _stage_draft(
            STAGES[1], executor_id="kimi-code", executor_model="kimi-k3",
            verifier_id="dsh-verifier", verifier_model="deepseek-v4-pro",
        )
        result = handle_goal_prepare(
            _envelope(Method.GOAL_PREPARE, f"req-v5-stage2-{int(time.time())}", {
                "contract_id": contract_id, "goal_id": GOAL_ID,
                "stage_id": "stage-2", "draft": draft,
            }),
            conn=conn, now=_now(),
        )
        if not result.get("ok"):
            raise RuntimeError(result)
        update_contract_state(conn, contract_id=contract_id,
                              new_state=ContractState.ACTIVE, now=_now())
        print(f"[v5][stage-2] contract ACTIVE; executor=kimi-code: {contract_id}")
    finally:
        conn.close()


    daemon = get_daemon_status(ROOT)
    if not daemon.get("running"):
        started = spawn_daemon(ROOT, interval_seconds=2.0)
        if not started.get("ok"):
            raise RuntimeError(f"daemon spawn failed: {started}")
    print("[v5][stage-2] waiting for kimi executor + independent verifier")
    conn = _conn()
    try:
        for _ in range(180):
            time.sleep(10)
            rows = conn.execute(
                "SELECT attempt_id, role, executor_id, state FROM attempts "
                "WHERE goal_id=? ORDER BY admitted_at", (GOAL_ID,)
            ).fetchall()
            if any(r[1] == "executor" and r[2] == "kimi-code" for r in rows):
                print(f"[v5][stage-2] kimi attempt observed: {rows}")
                if any(r[3] == "succeeded" for r in rows if r[1] == "verifier"):
                    return
            if any(r[3] == "blocked" for r in rows):
                break
        print(f"[v5][stage-2] timeout; inspect status: {rows}")
    finally:
        conn.close()


def plan_stage2() -> None:
    """不启动外部 CLI，只预演 stage-2 的 authority 选择。"""
    from longtask.adapters.registry import ExecutorRegistry
    from longtask.rpc.handlers._common import parse_contract_draft

    registry_path = ROOT / "registry.json"
    if not registry_path.is_file():
        raise RuntimeError("registry missing; run build-registry first")
    registry = ExecutorRegistry.load_from_file(registry_path)
    draft = parse_contract_draft(
        _stage_draft(
            STAGES[1], executor_id="kimi-code", executor_model="kimi-k3",
            verifier_id="dsh-verifier", verifier_model="deepseek-v4-pro",
        )
    )
    executor_ids = [entry.id for entry in registry.match_candidates(draft, requested_role="executor")]
    verifier_ids = [entry.id for entry in registry.match_candidates(draft, requested_role="verifier")]
    print(json.dumps({
        "stage": "stage-2",
        "external_process_started": False,
        "executor_candidates": executor_ids,
        "verifier_candidates": verifier_ids,
        "expected": {"executor": "kimi-code", "verifier": "dsh-verifier"},
    }, ensure_ascii=False, indent=2))
    if executor_ids != ["kimi-code"] or verifier_ids != ["dsh-verifier"]:
        raise RuntimeError("stage-2 authority preflight failed: default-deny mismatch")

def run_stage3() -> None:
    """阶段 3：重启 daemon 后再立约，验证 Goal/事件/阶段状态无损。"""
    import time

    daemon_before = get_daemon_status(ROOT)
    if daemon_before.get("running"):
        halt_daemon(ROOT)
        for _ in range(30):
            time.sleep(1)
            if not get_daemon_status(ROOT).get("running"):
                break
    restarted = spawn_daemon(ROOT, interval_seconds=2.0)
    if not restarted.get("ok"):
        raise RuntimeError(f"daemon restart failed: {restarted}")
    print(f"[v5][stage-3] daemon restarted pid={restarted['pid']}")

    conn = _conn()
    try:
        contract_id = f"{GOAL_ID}-stage3"
        goal = get_goal(conn, GOAL_ID)
        if goal is None:
            raise RuntimeError("goal not found; run setup first")
        _require_current_stage(goal, "stage-3")
        result = handle_goal_prepare(
            _envelope(Method.GOAL_PREPARE, f"req-v5-stage3-{int(time.time())}", {
                "contract_id": contract_id, "goal_id": GOAL_ID,
                "stage_id": "stage-3",
                "draft": _stage_draft(STAGES[2], executor_id="dsh-headless",
                                       verifier_id="dsh-verifier"),
            }),
            conn=conn, now=_now(),
        )
        if not result.get("ok"):
            raise RuntimeError(result)
        from longtask.contracts.schema import ContractState
        from longtask.persistence.store import update_contract_state
        update_contract_state(conn, contract_id=contract_id,
                              new_state=ContractState.ACTIVE, now=_now())
        goal = get_goal(conn, GOAL_ID)
        assert goal is not None
        print(f"[v5][stage-3] state survived restart; goal revision={goal['revision']}")
    finally:
        conn.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "setup":
        setup()
    elif cmd == "status":
        status()
    elif cmd == "stage1":
        run_stage1()
    elif cmd == "stage1-verify":
        run_stage1_verify()
    elif cmd == "stage2":
        run_stage2()
    elif cmd == "stage2-plan":
        plan_stage2()
    elif cmd == "stage3":
        run_stage3()
    elif cmd == "build-registry":
        build_registry()
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)
