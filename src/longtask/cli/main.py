"""longtask CLI 控制面入口（DESIGN §11.1、§11.2、§15.2 Developer Preview）。

提供：
1. `doctor`：系统自检与诊断；
2. 合同生命周期命令（prepare/approve/get/list/patch/pause/resume/cancel/arbitrate）；
3. `executor`：执行器注册与框定控制；
4. `kill-switch`：全局 Emergency Stop 熔断控制；
5. `rebuild`：从数据库重建文件投影；
6. `status` / `start` / `stop`：守护进程起停控制；
7. 全局 `--dry-run` 模拟执行与 `--data-dir` 隔离测试。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from longtask import PROTOCOL_VERSION, __version__
from longtask.adapters.registry import ExecutorRegistry
from longtask.cli.daemon import (
    DEFAULT_TICK_INTERVAL_SECONDS,
    get_daemon_status,
    halt_daemon,
    is_kill_switch_active,
    run_daemon_loop,
    set_kill_switch,
    spawn_daemon,
)
from longtask.cli.doctor import run_doctor
from longtask.cli.paths import default_data_root, migrate_data_dir
from longtask.persistence.projections import rebuild_projection, revert_projection
from longtask.persistence.store import StoreConfig, connect, ensure_schema
from longtask.rpc.errors import RpcError
from longtask.rpc.methods import Method
from longtask.rpc.server import RequestEnvelope, route


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="longtask",
        description=f"远期任务协议控制面 CLI (v{__version__}, protocol v{PROTOCOL_VERSION})",
    )
    parser.add_argument("--version", action="store_true", help="打印包与协议版本")
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="覆盖默认数据存储目录 (~/.longtask)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="演练模式：仅打印 RPC 请求参数，不写库",
    )

    sub = parser.add_subparsers(dest="command")

    # doctor
    sub.add_parser("doctor", help="运行系统自检（解释器、存储、数据库、注册表、熔断开关）")

    # status / start / stop
    sub.add_parser("status", help="查看守护进程与全局熔断状态")
    start_p = sub.add_parser("start", help="启动 longtaskd 调度守护进程（分离后台进程）")
    start_p.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_TICK_INTERVAL_SECONDS,
        help="调度扫描间隔秒数（默认 60）",
    )
    sub.add_parser("stop", help="停止 longtaskd 调度守护进程")
    # P6：数据目录迁移——安全默认：不带 --execute 只打印计划不动数据
    migrate_p = sub.add_parser(
        "migrate",
        help="迁移数据目录 ~/.longtask → ~/.lhgp（默认 dry-run；--execute 才真跑）",
    )
    migrate_p.add_argument(
        "--execute",
        action="store_true",
        help="真跑迁移（备份 + 拷贝式可回滚）；不带此标志只打印计划",
    )
    # 内部命令：常驻主循环入口，仅由 start 以分离进程方式调用
    daemonrun_p = sub.add_parser("_daemon-run", help=argparse.SUPPRESS)
    daemonrun_p.add_argument("--interval", type=float, default=DEFAULT_TICK_INTERVAL_SECONDS)

    # prepare
    prep_p = sub.add_parser("prepare", help="起草新远期任务合同（drafted）")
    prep_p.add_argument("--file", type=str, help="从 JSON/YAML 合同草稿文件读取")
    prep_p.add_argument("--contract-id", type=str, default=None, help="自定义合同 ID（可选）")
    prep_p.add_argument("--title", type=str, help="合同标题")
    prep_p.add_argument("--objective", type=str, help="完成标准描述（冻结区）")
    prep_p.add_argument("--deadline", type=str, help="截止墙钟时间 (ISO 8601)")
    prep_p.add_argument("--workload-hours", type=float, default=4.0, help="预估工时（小时）")

    # approve
    app_p = sub.add_parser("approve", help="批准合同进入激活状态（drafted -> active）")
    app_p.add_argument("contract_id", type=str, help="合同 ID")
    app_p.add_argument("--revision", type=int, default=None, help="期望版本号 (CAS)")

    # get
    get_p = sub.add_parser("get", help="查看指定合同当前状态与详情")
    get_p.add_argument("contract_id", type=str, help="合同 ID")

    # list
    list_p = sub.add_parser("list", help="列出合同列表")
    list_p.add_argument(
        "--state",
        type=str,
        default=None,
        help="按状态过滤 (drafted/active/paused/blocked/complete/expired/cancelled)",
    )
    list_p.add_argument("--limit", type=int, default=20, help="返回数量上限")
    list_p.add_argument("--cursor", type=str, default=None, help="分页游标")
    list_p.add_argument(
        "--verbose",
        action="store_true",
        help="显示 u（紧迫度）、blocked_reason、ETA 等附加字段",
    )
    list_p.add_argument(
        "--min-u",
        type=float,
        default=None,
        help="按紧迫度下界过滤（仅 u>=min-u 显示）",
    )

    # patch
    patch_p = sub.add_parser("patch", help="修订合同可变字段（soft_guidance/acceptance/workload）")
    patch_p.add_argument("contract_id", type=str, help="合同 ID")
    patch_p.add_argument("--revision", type=int, required=True, help="期望当前版本号 (CAS 强制)")
    patch_p.add_argument("--guidance", type=str, default=None, help="软指引内容（JSON 字符串）")
    patch_p.add_argument("--workload-hours", type=float, default=None, help="修正后的剩余工时")

    # pause / resume / cancel
    pause_p = sub.add_parser("pause", help="暂停运行中的合同 (active -> paused)")
    pause_p.add_argument("contract_id", type=str, help="合同 ID")

    resume_p = sub.add_parser("resume", help="恢复暂停或阻塞的合同 (paused/blocked -> active)")
    resume_p.add_argument("contract_id", type=str, help="合同 ID")

    cancel_p = sub.add_parser("cancel", help="终止合同 (-> cancelled)")
    cancel_p.add_argument("contract_id", type=str, help="合同 ID")
    cancel_p.add_argument("--reason", type=str, default="user cancelled via CLI", help="终止原因")

    # arbitrate
    arb_p = sub.add_parser("arbitrate", help="对 expired/blocked 合同执行人工裁决")
    arb_p.add_argument("contract_id", type=str, help="合同 ID")
    arb_p.add_argument(
        "--decision",
        type=str,
        required=True,
        choices=["complete", "archived", "active"],
        help="裁决目标状态",
    )
    arb_p.add_argument("--note", type=str, default=None, help="裁决附注说明")

    # kill-switch
    ks_p = sub.add_parser("kill-switch", help="全局 Emergency Stop 熔断控制")
    ks_group = ks_p.add_mutually_exclusive_group(required=True)
    ks_group.add_argument("--activate", action="store_true", help="立即激活全局熔断，停止一切派工")
    ks_group.add_argument("--deactivate", action="store_true", help="解除全局熔断")
    ks_group.add_argument("--check", action="store_true", help="查看熔断开关状态")

    # rebuild
    reb_p = sub.add_parser("rebuild", help="从权威库事件与状态强制重建文件投影")
    reb_p.add_argument("contract_id", type=str, help="合同 ID")
    reb_p.add_argument("--revert", action="store_true", help="丢弃盘上草稿改动以库为准强制回滚")

    watch_p = sub.add_parser(
        "watch", help="事件流 tail（只读；可过滤 contract/executor/kinds，支持 --follow）"
    )
    watch_p.add_argument("--contract", type=str, default=None)
    watch_p.add_argument("--executor", type=str, default=None)
    watch_p.add_argument("--since", type=int, default=None)
    watch_p.add_argument("--kinds", type=str, default=None)
    watch_p.add_argument("--for", type=int, default=None, dest="duration")
    watch_p.add_argument("--follow", action="store_true")

    # executor
    exec_p = sub.add_parser("executor", help="执行器资源池管理")
    exec_sub = exec_p.add_subparsers(dest="executor_cmd")
    exec_list = exec_sub.add_parser("list", help="列出已登记执行器")
    exec_list.add_argument("--enabled-only", action="store_true", help="仅显示已启用执行器")

    exec_en = exec_sub.add_parser("enable", help="启用指定执行器进入分发池")
    exec_en.add_argument("executor_id", type=str, help="执行器 ID")

    exec_dis = exec_sub.add_parser("disable", help="禁用指定执行器")
    exec_dis.add_argument("executor_id", type=str, help="执行器 ID")

    exec_h = exec_sub.add_parser("health", help="检查执行器健康与配置")
    exec_h.add_argument("executor_id", type=str, help="执行器 ID")

    return parser


def _dispatch_rpc(
    method: Method,
    params: dict[str, Any],
    *,
    data_dir: Path,
    dry_run: bool = False,
    now: datetime | None = None,
) -> int:
    """包装 CLI 向本机 RPC 服务端发送请求。"""
    envelope = RequestEnvelope(
        method=method,
        request_id=f"cli-req-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
        client_id="longtask-cli",
        protocol_version=PROTOCOL_VERSION,
        params=params,
    )

    if dry_run:
        print("[dry-run] simulated RPC request:")
        print(f"  method:     {envelope.method.value}")
        print(f"  request_id: {envelope.request_id}")
        print(f"  params:     {json.dumps(envelope.params, ensure_ascii=False, indent=2)}")
        return 0

    db_path = data_dir / "state.db"
    reg_path = data_dir / "registry.json"
    conn = connect(StoreConfig(db_path=db_path))
    try:
        ensure_schema(conn)
        registry = ExecutorRegistry.load_from_file(reg_path)
        resp = route(envelope, conn=conn, now=now, registry=registry)
        if resp.get("ok"):
            print(json.dumps(resp["result"], ensure_ascii=False, indent=2))
            return 0
        print(f"Error: {resp}", file=sys.stderr)
        return 1
    except RpcError as exc:
        print(f"Error [{exc.code.value}]: {exc.message}", file=sys.stderr)
        if exc.details:
            print(f"Details: {json.dumps(exc.details, ensure_ascii=False)}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"longtask {__version__} (protocol v{PROTOCOL_VERSION})")
        return 0

    if not args.command:
        parser.print_help()
        return 0

    root = Path(args.data_dir).expanduser().resolve() if args.data_dir else default_data_root()
    root.mkdir(parents=True, exist_ok=True)
    dry_run = bool(args.dry_run)

    # P6：数据目录迁移（安全默认：不带 --execute 只 dry-run）
    if args.command == "migrate":
        execute = bool(getattr(args, "execute", False))
        plan = migrate_data_dir(dry_run=not execute)
        print(plan.format_text())
        return 1 if any("FAILED" in s for s in plan.skipped) else 0

    # 1. doctor
    if args.command == "doctor":
        report = run_doctor(root)
        print(report.format_text())
        return 0 if report.all_ok else 1

    # 2. status / start / stop
    if args.command == "status":
        status = get_daemon_status(root)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0

    if args.command == "start":
        res = spawn_daemon(root, interval_seconds=args.interval)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.command == "stop":
        res = halt_daemon(root)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.command == "_daemon-run":
        res = run_daemon_loop(root, interval_seconds=args.interval, emit_fn=print)
        print(json.dumps(res, ensure_ascii=False))
        return 0

    # 3. kill-switch
    if args.command == "kill-switch":
        if args.activate:
            set_kill_switch(root, True)
            print("[kill-switch] ACTIVE: all dispatches halted.")
            return 0
        if args.deactivate:
            set_kill_switch(root, False)
            print("[kill-switch] inactive: normal operations resumed.")
            return 0
        if args.check:
            active = is_kill_switch_active(root)
            print(f"[kill-switch] status: {'ACTIVE (halted)' if active else 'inactive'}")
            return 0

    # 4. rebuild
    if args.command == "rebuild":
        conn = connect(StoreConfig(db_path=root / "state.db"))
        try:
            ensure_schema(conn)
            if args.revert:
                paths = revert_projection(root, args.contract_id, conn)
                print(f"[rebuild] reverted from database ({len(paths)} files materialized).")
            else:
                paths = rebuild_projection(root, args.contract_id, conn)
                print(f"[rebuild] projections materialized: {list(paths.keys())}")
            return 0
        finally:
            conn.close()

    if args.command == "watch":
        from longtask.cli.watch import main as _watch_main

        argv = []
        if args.contract:
            argv += ["--contract", args.contract]
        if args.executor:
            argv += ["--executor", args.executor]
        if args.since is not None:
            argv += ["--since", str(args.since)]
        if args.kinds:
            argv += ["--kinds", args.kinds]
        if args.duration is not None:
            argv += ["--for", str(args.duration)]
        if args.follow:
            argv += ["--follow"]
        return _watch_main(argv)

    # 5. 合同生命周期命令
    if args.command == "prepare":
        draft_dict: dict[str, Any] = {}
        if args.file:
            f_path = Path(args.file)
            content = f_path.read_text(encoding="utf-8")
            draft_dict = json.loads(content)
        else:
            if not args.title or not args.objective or not args.deadline:
                msg = "Error: prepare requires --file or (--title, --objective, --deadline)"
                print(msg, file=sys.stderr)
                return 2
            draft_dict = {
                "title": args.title,
                "objective": args.objective,
                "deadline_at": args.deadline,
                "workload_initial_hours": args.workload_hours,
                "hard_constraints": {"file_effects": {"mode": "workspace-write"}},
                "acceptance": {"standard": "验收标准通过", "checks": ["核对项 1"]},
                "budget": {
                    "max_dispatches": 5,
                    "max_escalations": 2,
                    "max_concurrent_attempts": 1,
                    "max_attempt_minutes": 60,
                    "max_output_bytes": 1048576,
                },
            }
        params: dict[str, Any] = {"draft": draft_dict}
        if args.contract_id:
            params["contract_id"] = args.contract_id
        return _dispatch_rpc(Method.CONTRACT_PREPARE, params, data_dir=root, dry_run=dry_run)

    if args.command == "approve":
        params = {"contract_id": args.contract_id}
        if args.revision is not None:
            params["expected_revision"] = args.revision
        return _dispatch_rpc(Method.CONTRACT_APPROVE, params, data_dir=root, dry_run=dry_run)

    if args.command == "get":
        return _dispatch_rpc(
            Method.CONTRACT_GET,
            {"contract_id": args.contract_id},
            data_dir=root,
            dry_run=dry_run,
        )

    if args.command == "list":
        if not args.verbose:
            # 简版走 RPC（薄）：仅核心字段
            params = {"limit": args.limit}
            if args.state:
                params["state"] = args.state
            if args.cursor:
                params["cursor"] = args.cursor
            return _dispatch_rpc(Method.CONTRACT_LIST, params, data_dir=root, dry_run=dry_run)

        # 详细版：直读 store + 计算 u/ETA（避免污染协议输出）
        from longtask.cli.formatting import now_utc, render_contract_list_verbose
        from longtask.persistence.store import list_contracts

        conn = connect(StoreConfig(db_path=root / "state.db"))
        ensure_schema(conn)
        try:
            from longtask.contracts.schema import ContractState

            state_filter: str | ContractState | None = args.state
            if state_filter:
                try:
                    state_filter = ContractState(state_filter)
                except ValueError:
                    # 未知状态名：如实降级为字符串匹配（不崩、可能零结果）
                    print(
                        f"[list] unknown state '{args.state}'; filtering as raw string",
                        file=sys.stderr,
                    )
            contracts = list_contracts(
                conn,
                state=state_filter,
                after_contract_id=args.cursor,
                limit=args.limit,
            )
            output = render_contract_list_verbose(contracts, min_u=args.min_u, now=now_utc())
        finally:
            conn.close()
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.command == "patch":
        params = {"contract_id": args.contract_id, "expected_revision": args.revision}
        if args.guidance:
            params["soft_guidance"] = json.loads(args.guidance)
        if args.workload_hours is not None:
            params["workload_initial_hours"] = args.workload_hours
        return _dispatch_rpc(Method.CONTRACT_PATCH, params, data_dir=root, dry_run=dry_run)

    if args.command == "pause":
        return _dispatch_rpc(
            Method.CONTRACT_PAUSE,
            {"contract_id": args.contract_id},
            data_dir=root,
            dry_run=dry_run,
        )

    if args.command == "resume":
        return _dispatch_rpc(
            Method.CONTRACT_RESUME,
            {"contract_id": args.contract_id},
            data_dir=root,
            dry_run=dry_run,
        )

    if args.command == "cancel":
        return _dispatch_rpc(
            Method.CONTRACT_CANCEL,
            {"contract_id": args.contract_id, "reason": args.reason},
            data_dir=root,
            dry_run=dry_run,
        )

    if args.command == "arbitrate":
        return _dispatch_rpc(
            Method.CONTRACT_ARBITRATE,
            {"contract_id": args.contract_id, "decision": args.decision, "note": args.note},
            data_dir=root,
            dry_run=dry_run,
        )

    # 6. executor 命令
    if args.command == "executor":
        if args.executor_cmd == "list":
            return _dispatch_rpc(
                Method.EXECUTOR_LIST,
                {"enabled_only": args.enabled_only},
                data_dir=root,
                dry_run=dry_run,
            )
        if args.executor_cmd == "enable":
            return _dispatch_rpc(
                Method.EXECUTOR_ENABLE,
                {"executor_id": args.executor_id},
                data_dir=root,
                dry_run=dry_run,
            )
        if args.executor_cmd == "disable":
            return _dispatch_rpc(
                Method.EXECUTOR_DISABLE,
                {"executor_id": args.executor_id},
                data_dir=root,
                dry_run=dry_run,
            )
        if args.executor_cmd == "health":
            return _dispatch_rpc(
                Method.EXECUTOR_HEALTH,
                {"executor_id": args.executor_id},
                data_dir=root,
                dry_run=dry_run,
            )
        parser.parse_args(["executor", "--help"])
        return 0

    return 0


def entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
