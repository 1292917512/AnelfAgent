# MCP 工具桥接实现说明

本文只在修改 `entities/mcp/`、MCP 服务热同步、工具 schema、连接重试或 OAuth 时阅读。频道和实体
通过统一注册表使用这套桥接，不应在业务模块复制一套 MCP 生命周期。

## 模块职责

| 文件 | 责任 |
|---|---|
| `bridge.py` | MCP 会话生命周期、连接状态、调用分发和存活探测 |
| `config.py` | 服务配置、启用状态、存储和沉睡策略 |
| `manage_tools.py` | 服务管理与工具管理入口 |
| `transport.py` | stdio/HTTP 传输创建、环境变量白名单和超时参数 |
| `schema.py` | 参数 schema 解析、默认值和工具名规范化 |
| `render.py` | 文本、结构化内容和多模态结果渲染 |
| `retry.py` | 连接重试和稳定窗口内的重试预算 |
| `oauth.py` | 自研 `McpOAuthProvider(httpx.Auth)` 两阶段授权（替换 SDK 托管） |

## 稳定契约

| 机制 | 位置 | 说明 |
|------|------|------|
| 结果内容块分派 | `MCPBridge._render_call_result` | text 拼接；**image 落盘（uploads/mcp/）+ `_multimodal` 约定**——视觉模型经 think_loop 注入直接「看到」MCP 截图，非视觉模型读路径占位；base64 原文绝不进上下文。audio/resource_link/embedded resource 短占位；无文本时 structuredContent 兜底。有图输出 `{"_multimodal": true, "text", "images"}` JSON，无图输出纯文本 |
| 工具列表热同步 | `message_handler` 注入 + `_sync_server_tools` | SDK 默认静默丢弃 `ToolListChangedNotification`；经 ClientSession 公开 `message_handler` 参数拦截（旧版 SDK 无此参数自动跳过）→ 1s 防抖 → **增量**增删注册（同名描述变更不动，避免无谓 tools 前缀缓存失效）。`mcp_tool_list_sync` 可关 |
| 注册超时对齐 | `_register_tool_entries(call_timeout=...)` | server 的 `call_timeout` 透传为工具执行超时 meta——连接生命周期超时与单次调用超时是两件事，不透传会落入全局默认 60s 提前掐断 |
| 参数 schema 保真 | `_parse_param_schema` | anyOf/oneOf 可选参数解引用取非 null 分支的 type；`default/items/minimum/...` 经 `schema_extra` 直通 wire schema（模型看到默认值与数组元素结构，不按 string 兜底猜） |
| 注册名整形 | `_sanitize_tool_name` | 冲突检测在整形后的名字上进行；非法字符替换下划线 + 64 字符上限（超限截断 + SHA-256 前 8 位防撞）——OpenAI 风格端点会拒绝整组 tools 数组，一个坏名字可导致全会话不可用 |
| 结构化错误 | `call_tool`/`_do_call_tool` | 未命中 → not_found；超时 → timeout + `code=TOOL_TIMEOUT` + retryable；断线 → network + retryable（对齐 core/tool_errors 纪律） |
| 重连预算复位 | `_RetryBudget`（稳定窗口 300s） | 连接稳定运行超窗口后失败，重试计数清零重计——长期服务偶发抖动不累计耗尽 5 次预算而永久死亡；退避序列 1/2/4/8/16s |
| 连接存活探测 | `bridge._wait_with_liveness`（lifecycle 等待段） | stdio 子进程退出/网络静默断开时 SDK 不通知等待方——按 `mcp_liveness_ping_seconds`（默认 60，0=关）周期 `send_ping`（10s 超时），失败抛 ConnectionError 进既有断线分支；enabled 走自动重连、disabled/已删除退出清理；`_try_reconnect` 带 enabled 守卫，禁用 server 调用失败只报错不复活拉起 |
| Web 启停切换语义 | `services.mcp.toggle_server` | 以配置文件 enabled 为准而非连接状态：已启用（无论是否连上）→ 禁用并断开；已禁用 → 启用 + 热重载连接，连不上保持启用落盘并如实回报 last_error（重启自动重试） |
| 装配重建触发器贯通 | `think_loop` 工具集版本元组 | 版本元组含 `EntityRegistry.version()`（classmethod 调用）——热同步/reload/WebUI 开关等注册表增删后，**回复进行中**的下一轮即重建 active_tools；重建经追加式冻结保持前缀字节稳定 |

## mcp 2.x 适配纪律

mcp>=2.2 为唯一支持版本（pyproject 下限钉住）：

- SDK 字段一律 snake_case 直读——`input_schema`/`is_error`/`structured_content`/`read_only_hint`/
  `mime_type`（camelCase 仅是 wire 别名，构造经 `model_validate` 解析后不存在同名属性，
  getattr 兜底静默失效曾致全工具参数丢失，**禁止回退双形态**）
- 通知为裸叶子实例 isinstance 判定（`ServerNotification` union 别名不可实例化）
- streamable_http：headers/timeout/auth 全部经 `httpx2.AsyncClient` 传入且外部 client 生命周期自持
  （组合 CM），timeout 映射对齐 SDK 语义（连接/写/池 = timeout，读 = sse_read_timeout）
- OAuth：`McpOAuthProvider` 基类 httpx2.Auth（httpx2 `_build_auth` 做 isinstance 校验，原版对象必拒），
  授权服务器 REST 调用仍走独立 httpx 客户端；`ClientSession.message_handler` 直注
- 测试构造一律真实 `mcp.types` 模型（`tests/helpers/mcp_fakes.py`），禁用 SimpleNamespace 字段 mock

## OAuth 全链路（entities/mcp/oauth.py）

两阶段设计：

**运行期**：401 **先刷新**、确定性失败才交互授权；403 insufficient_scope 并集 scope step-up 重授权；
每 server 单飞锁 + 最近刷新窗口合并（AS 不回 expires_in 时防轮转撞车）；错误分类 invalid_grant 清
token 留 client / 静态客户端 invalid_client 判配置错误 / 网络 5xx fail-soft 保留凭据。

**OAuth 进入门控（WWW-Authenticate Bearer）**：401 只有服务器明确发 `WWW-Authenticate: Bearer` 挑战
才进入刷新/授权流；**无凭据首轮裸发**（不在请求发出前预拉起授权事务——预授权会让静态凭据服务器
首连就误报「未声明 OAuth」）——带静态凭据（key 在 URL/headers）的服务器 401 是凭据问题，不做任何
OAuth 动作。**well-known 发现**候选全 404 或 200+非元数据文档 → 返回 None 拒绝发明端点；AS 元数据
缺失的遗留形态只推导 authorize/token 缺省端点，**不发明注册端点**（无 DCR 文档证据即报「不支持
DCR，请静态配置 oauth.client_id」）。

**交互事务**：RFC9728 路径插入形+根回落 → RFC8414 OIDC 回落 + **issuer 校验拒绝**（RFC 9207：
回调 iss 与发现的 issuer 不一致即拒绝换码，防混淆代理）→ RFC7591 **每次授权重新注册**（redirect
端口随事务变化，旧注册会锁死）→ PKCE S256 + state。AS 元数据 URL 覆盖键 `oauth.auth_server_metadata_url`
是服务器 advertise 错误时的逃生门（跳过发现链直接使用配置文档）。**scope 沿用与回填**（RFC 6749 §6）：
refresh/换码响应省略 scope 时沿用旧值（视同维持原授权），不把已授 scope 抹掉。

WebUI 主动授权：`bridge.authorize_server`（URL 即返、回调后台等待、成功自动重连）+ `POST /mcp/{name}/oauth`
+ ServerCard 授权按钮 + `mcp_auth_start` 工具。

## 修改后检查

```powershell
uv run pytest entities/mcp/tests
uv run lint-imports
```

修改 MCP SDK 版本时，测试构造应使用真实 `mcp.types` 模型；同时核对 snake_case 模型字段、
streamable HTTP 超时映射和 OAuth 适配。
