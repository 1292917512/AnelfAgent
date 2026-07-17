# Cognee 记忆增强

Cognee 是可选的知识图谱投影与联邦召回后端。SQLite、FTS5 和现有
Embedding `MemoryStore` 始终是权威存储；Cognee 不替代 `EntityRegistry`。

## 安装与启用

```powershell
uv sync --extra cognee
```

安装完成后，在 WebUI 的“记忆管理 → 配置”中启用 Cognee，或修改
`config/cognee.json` 的 `enabled`。重启 Agent 后生效。Cognee 使用当前
AnelfAgent 默认聊天模型和 Embedding 模型，不需要重复保存 API Key。

默认数据目录是 `config/memory/cognee`，与现有 SQLite 文件完全隔离。
Cognee v1.3.0 仍处于 Beta，因此依赖固定到该版本。

## 一致性与降级

- 新增、更新和删除先提交到 SQLite，再写入持久化 outbox。
- 后台任务按数据集批量投影；失败会指数退避并保留，达到上限后可在
  WebUI 或 `retry_cognee_sync` 工具中重试。
- 召回使用加权 RRF 合并原生与 Cognee 结果，并按内容和来源标识去重。
- 未安装、未配置、超时或运行失败时自动返回原生召回结果。
- user/group scope 使用不可逆哈希数据集名隔离，默认查询 global 与当前
  scope，避免跨用户或跨群组召回。

## 历史数据与回滚

历史记忆不会自动迁移。管理 API
`POST /memory/cognee/backfill` 支持先 `dry_run`，确认后再显式入队。
禁用 Cognee 后重启即可回到纯原生记忆系统，SQLite 数据不受影响。

`prune`、`delete_all` 等 Cognee 全局危险方法只存在于程序门面，不注册为
普通 Agent 工具。
