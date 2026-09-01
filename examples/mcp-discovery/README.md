# MCP server e2e 复验（DESIGN §11.1、§17）

日期：2026-09-01。本目录是 MCP server 与 §17 skill 接入的端到端归档。

## 范围

- 任何 MCP 兼容的 agent harness（Claude Desktop / DSH / agent-zero 等）通过
  stdio JSON-RPC 2.0 调用 `longtask-mcp` 即可发现并使用长任务协议。
- 本目录的 `mcp-trace.example.log` 是一次完整 e2e 复验日志：8 步走通
  合同生命周期。

## 暴露的 MCP 工具（精选 7 个，不是 24 个 RPC 透传）

| 工具 | 用途 |
|---|---|
| `longtask_health` | 协议探活 + 工具清单（模型第一步） |
| `longtask_list_executors` | 选执行器 |
| `longtask_prepare_contract` | 立合同（schema 自描述，立约必填字段在工具 description 里） |
| `longtask_approve_contract` | 批准进入调度 |
| `longtask_get_contract` | 查询单份合同 |
| `longtask_list_contracts` | 列表 |
| `longtask_attach_to_executor` | 执行者认领 attempt + 读 §4.1 上下文快照 + 可选写回 |

每个工具的 `inputSchema` 包含 `required` 字段与字段说明；模型从 schema
自学"传什么"。§17 skill（`skills/longtask-contract/SKILL.md`）给完整
业务背景与示例。

## e2e trace（节选）

```
1) initialize: longtask-mcp/0.1.0a0
2) tools/list: 7 个 longtask_* 工具全部发现
3) longtask_health: protocol=1
4) longtask_list_executors: [exec-a, exec-b]
5) longtask_prepare_contract: cid=lt-20260901-mcpe2e state=drafted
6) longtask_approve_contract: ok=True
7) longtask_get_contract: state=active
8) longtask_attach_to_executor: lease.holder=att-mcp-e2e
   write_back.gen=1 events=[4, 5]
```

事件链：`contract/prepared → approved → started → context/snapshot → attempt/succeeded`。

## 已知边界

- **DSH 客户端的 MCP server 注册不在本协议范围**：每个 MCP 客户端
  需独立配置 longtask-mcp 路径（stdio 命令）。配置 DSH 客户端的
  MCP 插件是 DSH 侧能力。
- **stdio pipe 编码**：Windows 上 stdin/stdout 透明用系统编码（cp936）转换
  utf-8 字节。`longtask-mcp` 强制 `ensure_ascii=True` 输出，模型侧按
  UTF-8 解析 JSON 字符串 `\u` 转义即可——编码无关。

## 复现

```bash
uv sync --extra dev
PYTHONPATH=src python -m longtask.mcp_server --data-dir /tmp/mcp-test

# 在另一个终端用任何 MCP 客户端或自己手写 stdio 帧：
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}\n' | \
  PYTHONPATH=src python -m longtask.mcp_server --data-dir /tmp/mcp-test
```

## 相关

- `../../skills/longtask-contract/SKILL.md`：§17 skill（教模型怎么用协议）
- `../../src/longtask/mcp_server.py`：MCP server 实现
- `../../tests/integration/test_mcp_server.py`：5 个 stdio 集成测试
- `../../quality/evidence/gate-run-20260901-context-verifier.md`：上一轮门 evidence
- `../../quality/claims.json` `mcp-server-and-skill` 条目：治理真相源
