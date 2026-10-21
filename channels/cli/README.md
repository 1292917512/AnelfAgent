# CLI 频道

`cli` 使用标准 `CONFIG_MODEL`，默认停用。它把文本输出到进程终端；在普通服务中启用时，
不会自动读取标准输入。单终端目标默认为 `cli_user`；其他目标返回发送失败。

运行完整应用的终端交互模式：

```powershell
uv run python -m channels.cli
```

该入口启用 CLI、关闭 WebUI，并复用应用的启动与关停生命周期；其他已启用频道仍按配置
启动。输入 `exit`、`quit`、`q` 或发送 EOF 即退出。配置通过 `adapter/cli` 组管理；
`bot_name` 留空时从人设读取。
