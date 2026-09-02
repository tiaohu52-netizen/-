"""P6 插件包（SPEC §14）冒烟测试。

只验证静态清单：.codex-plugin/plugin.json 与 .mcp.json 存在、合法
JSON，并符合官方插件清单的 companion path 形状。
不改 pyproject 也不动代码（警惕累赘）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longtask import __version__

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestPluginManifest:
    def test_plugin_json_exists_and_is_valid_json(self) -> None:
        path = REPO_ROOT / ".codex-plugin" / "plugin.json"
        assert path.is_file(), f"missing {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == "lhgp"
        assert data["version"] == __version__
        assert data["skills"] == "skills"
        assert data["mcpServers"] == ".mcp.json"
        assert data["author"]["name"] == "LHGP maintainers"
        assert data["interface"]["displayName"] == "远期目标协议"

    def test_plugin_referenced_skill_path_exists(self) -> None:
        data = json.loads((REPO_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        skill_path = REPO_ROOT / data["skills"] / "long-horizon-goals" / "SKILL.md"
        assert skill_path.is_file(), f"missing skill at {skill_path}"
        skill = skill_path.read_text(encoding="utf-8")
        assert "`lhgp-mcp`" in skill

    def test_plugin_referenced_mcp_config_exists(self) -> None:
        data = json.loads((REPO_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        mcp_path = REPO_ROOT / data["mcpServers"]
        assert mcp_path.is_file(), f"missing mcp config at {mcp_path}"

    def test_legacy_skill_manifest_matches_protocol(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "skills" / "longtask-contract" / "MANIFEST.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["version"] == __version__
        assert manifest["protocol_version"] == "lhgp/v1alpha1"


class TestMcpConfig:
    def test_mcp_json_valid_with_lhgp_server(self) -> None:
        path = REPO_ROOT / ".mcp.json"
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "mcpServers" in data
        assert "lhgp" in data["mcpServers"]
        server = data["mcpServers"]["lhgp"]
        assert server["type"] == "stdio"
        assert server["command"] == "lhgp-mcp"


class TestEntryPointAlignment:
    def test_legacy_entry_points_preserved(self) -> None:
        """P6 范围说明：longtask / longtaskd 旧名保留至 P6 末尾再迁移。

        本阶段：插件 manifest 引用的命令必须与 pyproject scripts 一致。
        """
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "longtask = " in pyproject
        assert "longtaskd = " in pyproject
        assert "longtask-mcp = " in pyproject

    def test_wheel_includes_plugin_companion_resources(self) -> None:
        """wheel 不能退化成只含 runtime 的包，必须携带模型接入资源。"""
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for resource in (
            '".codex-plugin/plugin.json" = ".codex-plugin/plugin.json"',
            '".mcp.json" = ".mcp.json"',
            '"skills/long-horizon-goals/SKILL.md" = "skills/long-horizon-goals/SKILL.md"',
        ):
            assert resource in pyproject
