#!/usr/bin/env python3
"""质量声明注册表门（CONTRIBUTING「质量声明注册表」）。

校验：
1. claims.json 符合 quality/claim-schema.json（jsonschema 权威校验）。
   v2 注册表同时声明 design_claims 与 implementation_claims 两桶。
2. verified 声明的每条证据 path 必须是仓库相对路径且验证时存在。
3. 声明 id 在 design_claims + implementation_claims 之间唯一；scope_paths 必须是仓库相对。
4. blocking 生命周期的声明存在 → 门红（它语义上就是阻断）。
5. deferred 只打印计数——欠着可见，但不阻断骨架期推进。
6. design_claims 桶里 evidence.kind 只允许 source_static / manual_review（事实层声明）。
7. implementation_claims 桶里要求根级 pinned_sha 不是 "unpinned"（事实必须锚定真实 commit）。
fail-closed：jsonschema 未安装、文件缺失、解析失败 → 报错退出。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "quality" / "claims.json"
SCHEMA = REPO_ROOT / "quality" / "claim-schema.json"


def is_repo_relative(path: str) -> bool:
    p = Path(path)
    return not p.is_absolute() and ".." not in p.parts


def main() -> int:
    try:
        import jsonschema
    except ImportError:
        print(
            "[claims] jsonschema not installed; refusing to pass. Run: uv sync --extra dev",
            file=sys.stderr,
        )
        return 1

    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[claims] cannot read registry/schema (fail-closed): {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(registry), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"schema: {loc}: {err.message}")

    # v1/v2 双形态：v1 只有 claims；v2 必有 design_claims + implementation_claims。
    # 校验按合并视图走，重复 id 在两桶之间也必须唯一。
    if not isinstance(registry, dict):
        errors.append("registry: top-level must be an object")
        registry = {}

    registry_version = registry.get("registry_version")
    design_claims = list(registry.get("design_claims", [])) if registry_version == 2 else []
    implementation_claims = (
        list(registry.get("implementation_claims", [])) if registry_version == 2 else []
    )
    legacy_claims = list(registry.get("claims", [])) if registry_version == 1 else []

    if registry_version == 2:
        if not design_claims and not implementation_claims and not legacy_claims:
            errors.append(
                "registry: v2 requires at least one of design_claims or implementation_claims"
            )
        pinned_sha = registry.get("pinned_sha")
        if implementation_claims and pinned_sha == "unpinned":
            errors.append(
                "registry: implementation_claims present but pinned_sha is 'unpinned'; "
                "implementation evidence must anchor to a real 40-char commit SHA"
            )

    claims = design_claims + implementation_claims + legacy_claims
    seen_ids: set[str] = set()
    deferred = 0
    blocking = 0

    # 设计/实现桶各自允许的 evidence.kind
    design_only_kinds = {"source_static", "manual_review"}
    implementation_allowed_kinds = {
        "focused_test",
        "integration_real_store",
        "ci",
        "crash_recovery",
        "gate_run",
        "manual_review",
        "source_static",
    }

    for bucket, bucket_name in (
        (design_claims, "design_claims"),
        (implementation_claims, "implementation_claims"),
    ):
        for claim in bucket:
            cid = claim.get("id", "<missing>")
            if cid in seen_ids:
                errors.append(f"duplicate claim id: {cid}")
            seen_ids.add(cid)

            lifecycle = claim.get("lifecycle")
            if lifecycle == "deferred":
                deferred += 1
            elif lifecycle == "blocking":
                blocking += 1
                errors.append(f"claim '{cid}' is blocking: {claim.get('statement', '')[:60]}")

            allowed = (
                design_only_kinds
                if bucket_name == "design_claims"
                else implementation_allowed_kinds
            )
            for ev in claim.get("evidence", []):
                path = ev.get("path")
                kind = ev.get("kind")
                if kind is not None and kind not in allowed:
                    errors.append(
                        f"claim '{cid}' ({bucket_name}): evidence kind '{kind}' not allowed "
                        f"in {bucket_name} (allowed: {sorted(allowed)})"
                    )
                if path is None:
                    errors.append(f"claim '{cid}': evidence without path (kind={kind})")
                    continue
                if not is_repo_relative(path):
                    errors.append(f"claim '{cid}': evidence path not repo-relative: {path}")
                    continue
                if not (REPO_ROOT / path).exists():
                    errors.append(f"claim '{cid}': evidence missing at validation time: {path}")

            for scope in claim.get("scope_paths", []):
                if not is_repo_relative(scope):
                    errors.append(f"claim '{cid}': scope path not repo-relative: {scope}")

    for claim in legacy_claims:
        cid = claim.get("id", "<missing>")
        if cid in seen_ids:
            errors.append(f"duplicate claim id: {cid}")
        seen_ids.add(cid)

        lifecycle = claim.get("lifecycle")
        if lifecycle == "deferred":
            deferred += 1
        elif lifecycle == "blocking":
            blocking += 1
            errors.append(f"claim '{cid}' is blocking: {claim.get('statement', '')[:60]}")

        for ev in claim.get("evidence", []):
            path = ev.get("path")
            if path is None:
                errors.append(f"claim '{cid}': evidence without path (kind={ev.get('kind')})")
                continue
            if not is_repo_relative(path):
                errors.append(f"claim '{cid}': evidence path not repo-relative: {path}")
                continue
            if not (REPO_ROOT / path).exists():
                errors.append(f"claim '{cid}': evidence missing at validation time: {path}")

        for scope in claim.get("scope_paths", []):
            if not is_repo_relative(scope):
                errors.append(f"claim '{cid}': scope path not repo-relative: {scope}")

    if errors:
        print("[claims] violations:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    total = len(claims)
    verified = sum(1 for c in claims if c.get("lifecycle") == "verified")
    design_total = len(design_claims)
    impl_total = len(implementation_claims)
    legacy_total = len(legacy_claims)
    print(
        f"[claims] OK: {total} claims "
        f"(design={design_total}, implementation={impl_total}, legacy={legacy_total}; "
        f"{verified} verified, {deferred} deferred, {blocking} blocking); "
        f"registry version={registry_version}, pinned_sha={registry.get('pinned_sha')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
