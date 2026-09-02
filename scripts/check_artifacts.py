"""Verify that built distributions retain the plugin companion resources."""

from __future__ import annotations

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
        print(f"{artifact}: companion resources OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
