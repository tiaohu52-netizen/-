"""Verify that built distributions retain the plugin companion resources."""

from __future__ import annotations

import json
import re
import sys
import tarfile
import zipfile
from pathlib import Path

REQUIRED = {
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "skills/long-horizon-goals/SKILL.md",
    "skills/longtask-contract/MANIFEST.json",
    "skills/longtask-contract/SKILL.md",
}
CANONICAL_ENTRYPOINTS = {
    "lhgp = lhgp.cli.main:entrypoint",
    "lhgpd = lhgp.cli.daemon_proc:lhgpd_entrypoint",
    "lhgp-mcp = lhgp.mcp_server:main",
}
_STRICT_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _names(path: Path) -> set[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return {name for name in archive.namelist() if name in REQUIRED}
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return {
                member.name.split("/", 1)[1]
                for member in archive.getmembers()
                if "/" in member.name and member.name.split("/", 1)[1] in REQUIRED
            }
    raise ValueError(f"unsupported artifact: {path}")


def _read_member(path: Path, target: str) -> bytes:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.read(target)
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                if "/" in member.name and member.name.split("/", 1)[1] == target:
                    extracted = archive.extractfile(member)
                    if extracted is not None:
                        return extracted.read()
    raise KeyError(f"{target} not found in {path}")


def _validate_companion_metadata(path: Path) -> str | None:
    try:
        plugin = json.loads(_read_member(path, ".codex-plugin/plugin.json"))
        mcp = json.loads(_read_member(path, ".mcp.json"))
    except (KeyError, json.JSONDecodeError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return f"invalid companion metadata: {exc}"
    version = plugin.get("version")
    if not isinstance(version, str) or _STRICT_SEMVER.fullmatch(version) is None:
        return f"plugin manifest version is not strict SemVer: {version!r}"
    if plugin.get("id") != "lhgp" or plugin.get("name") != "lhgp":
        return "plugin manifest id/name must both be 'lhgp'"
    server = mcp.get("mcpServers", {}).get("lhgp", {})
    if server.get("command") != "lhgp-mcp":
        return "MCP companion must expose the canonical 'lhgp-mcp' command"
    return None


def _validate_wheel_entrypoints(path: Path) -> str | None:
    if path.suffix != ".whl":
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_name = next(
                name for name in archive.namelist() if name.endswith(".dist-info/entry_points.txt")
            )
            entries = {
                line.strip()
                for line in archive.read(metadata_name).decode("utf-8").splitlines()
                if " = " in line
            }
    except (StopIteration, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        return f"wheel entry-point metadata is invalid: {exc}"
    missing = CANONICAL_ENTRYPOINTS - entries
    return f"wheel missing canonical entry points: {sorted(missing)}" if missing else None


def main() -> int:
    dist = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
    artifacts = sorted((*dist.glob("*.whl"), *dist.glob("*.tar.gz")))
    if not artifacts:
        print(f"no distribution artifacts found in {dist}", file=sys.stderr)
        return 1
    for artifact in artifacts:
        missing = REQUIRED - _names(artifact)
        if missing:
            print(f"{artifact}: missing {sorted(missing)}", file=sys.stderr)
            return 1
        metadata_error = _validate_companion_metadata(artifact)
        if metadata_error:
            print(f"{artifact}: {metadata_error}", file=sys.stderr)
            return 1
        entrypoint_error = _validate_wheel_entrypoints(artifact)
        if entrypoint_error:
            print(f"{artifact}: {entrypoint_error}", file=sys.stderr)
            return 1
        print(f"{artifact}: companion resources OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
