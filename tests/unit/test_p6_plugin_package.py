"""P6 插件包（SPEC §14）冒烟测试。

只验证静态清单：.codex-plugin/plugin.json 与 .mcp.json 存在、合法
JSON、引用到的 entry_points 命令名与已装 entry_points 对得上。
不改 pyproject 也不动代码（警惕累赘）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestPluginManifest:
    def test_plugin_json_exists_and_is_valid_json(self) -> None:
        path = REPO_ROOT / ".codex-plugin" / "plugin.json"
        assert path.is_file(), f"missing {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == "lhgp"
        assert data["entrypoints"]["cli"]["command"] == "longtask"
        assert data["entrypoints"]["mcp_server"]["command"] == "longtask-mcp"

    def test_plugin_referenced_skill_path_exists(self) -> None:
        data = json.loads((REPO_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        for skill in data["skills"]:
            skill_path = REPO_ROOT / skill["path"]
            assert skill_path.is_file(), f"missing skill at {skill_path}"

    def test_plugin_referenced_mcp_config_exists(self) -> None:
        data = json.loads((REPO_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        mcp_path = REPO_ROOT / data["mcp_config"]
        assert mcp_path.is_file(), f"missing mcp config at {mcp_path}"


class TestMcpConfig:
    def test_mcp_json_valid_with_lhgp_server(self) -> None:
        path = REPO_ROOT / ".mcp.json"
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "mcpServers" in data
        assert "lhgp" in data["mcpServers"]
        server = data["mcpServers"]["lhgp"]
        assert server["type"] == "stdio"
        assert server["command"] == "longtask-mcp"


class TestEntryPointAlignment:
    def test_legacy_entry_points_preserved(self) -> None:
        """P6 范围说明：longtask / longtaskd 旧名保留至 P6 末尾再迁移。

        本阶段：插件 manifest 引用的命令必须与 pyproject scripts 一致。
        """
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "longtask = " in pyproject
        assert "longtask-mcp = " in pyproject
