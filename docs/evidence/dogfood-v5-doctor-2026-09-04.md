# dogfood v5 本机环境预检（2026-09-04）

## 命令

```powershell
uv run lhgp --data-dir .dogfood doctor
```

## 结果

- Python runtime：通过（3.13.13）
- storage directory：通过
- SQLite `state.db`：通过
- executor registry：通过（3 enabled / 3 registered）
- kill switch：inactive
- 总结：`ALL SYSTEMS GO`

## 外部 CLI 现状

- `kimi --version`：`0.38.0`
- `dsh`：本机 PATH 中不存在
- dogfood registry 中的 DSH 启动项使用显式 Node.js 路径；doctor 能验证该
  启动入口存在，但不能替代 DSH provider 凭据和真实 verifier 运行。

## 结论

该预检证明本机协议存储、registry 与启动前诊断可用；不证明 stage-2/3
真实 CLI 接力或 verifier fail → repair → reverify 闭环。后续接入 DSH 后，
应重新运行 stage2、stage3，并把事件流与最终快照追加到本目录。
