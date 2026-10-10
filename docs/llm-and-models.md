# 模型与 LLM 层

模型配置管理、调用协议、韧性重试、用量口径与内部辅助调用的现状说明。修改 `agent/llm/`、
`entities/model_control/` 或用量统计时按需阅读；缓存排查见[上下文缓存分册](context-cache.md)。

## 模型配置与管理（llm_clients.json）

- **AI 可全生命周期管理模型配置**：add/update/remove_provider + add/remove_model（CRITICAL 走
  审批面，返回脱敏摘要、密钥永不回显），update_model_config 白名单含 enabled/model；
  Web 经模型页，双路径同 LLMManager 内存态 + 原子落盘即热生效
- **api_key 支持 `${ENV_VAR}` 引用**（两步写盘：引用串建档落盘后再写展开值，`_restore_env_refs`
  比对使磁盘保引用、内存持真值）
- **热重载 reconcile**（`LLMManager.reload_from_disk` + `_parse_config_data` 启动/重载共用解析）：
  ConfigWatcher mtime 轮询自动 / Web 接口 / AI 工具 reload_model_config 三路径共用——不变模型
  客户端零触碰（保运行时学习状态与连接池），变化经 `update_config` 原地变异（对象身份不变），
  新增构造、删除修正默认并热切换；进程内 save_config 自写触发监听时 diff 为空即 no-op；解析失败
  保留运行中配置（用户可能正在编辑）
- **思考契约声明**：模型配置的 `thinking` 字段声明思考契约（`{"param": 目标字段, "map": 档位映射,
  "on": 开启值, "off": 关闭值}`）。**全代码库对模型名零特判**——LLMClient 只做「读契约填值」，
  不认识任何模型名/供应商；档位能力不写代码，模型该用哪档由配置 `reasoning_effort` 决定

## 调用协议

### 对话协议路由（chat_protocol）

三值语义（`agent/llm/protocol.py` 能力矩阵 + `agent/llm/responses/router.py` + 
`llm_client._should_fallback_from_responses`）：

- `responses` = **绝对走官方 /responses 接口**（openai/azure 一律 native 直连，不支持是配置错误、
  404 原样上抛；anthropic 等无官方端点的 api_type 经 litellm bridge 桥接）
- `auto` = openai/azure 优先 native Responses，端点未实现（404，经 classifier NOT_FOUND 判定）时记
  客户端级标记 `_responses_native_blocked` 并回退 chat_completions（本进程内后续直连；流式路径
  已产出增量则禁止回退）
- `chat_completions` = 传统通道
- base_url 以 `/responses`、`/chat/completions` 结尾时 URL 推断优先于配置（`resolved_chat_protocol`）；
  bridge 的唯一正当用途 = 非 openai 系 api_type 的 Responses 暴露

### 思考等级下发

- 下发载体按 api_type 区分（litellm 行为差异）：openai 兼容通道 extra_body 由 SDK 展开进请求体顶层；
  anthropic 兼容通道直发 body 不展开 extra_body，故填顶层字段 + allowed_openai_params 白名单放行。
  无契约模型走通用 reasoning_effort 透传。effort 为空时开关型契约（无 map）用 on 值默认开启
- litellm 暗坑：未收录模型顶层 reasoning_effort 可能被 drop_params 静默丢弃，必须走 extra_body/
  白名单透传
- **Responses 路径（chat_protocol=responses/auto）不使用 thinking 契约**——effort 统一映射为
  Responses 的 `reasoning.effort` 下发，契约仅作用于 chat_completions 通道
- 档位表：`agent/llm/reasoning.py` 是思考等级单一权威（7 级规范词汇 + 各家专项档位表 +
  下发通道分派）；会话参数覆盖经 `get_session_llm_params` / `canonical_efforts`

## 韧性与重试

| 机制 | 位置 | 说明 |
|------|------|------|
| 错误分类 | `agent/llm/resilience/classifier.py` | rate_limit/context_overflow/auth/not_found 等分类驱动重试/压缩/回退策略 |
| 自适应退避 | `agent/llm/retry.py`（jittered_backoff） | 指数退避 + 抖动 |
| Retry-After 采信 | `retry.py::parse_retry_after` | litellm RateLimitError 携带 headers；支持秒数/HTTP 日期/毫秒变体。退避取 max(服务端指令, 本地抖动指数)；服务端要求 >60s（`RETRY_AFTER_WAIT_CAP`）视为本轮放弃当前候选转回退链 |
| 回退候选消息适配 | `llm_manager._messages_for_candidate` | Anthropic 线产生的协议专属字段（缓存断点 + 签名思考块）回退到 OpenAI 兼容端点时全剥（零拷贝快径，无字段原样返回、非破坏副本）；**reasoning_details 是 OpenRouter 风格字段不在剥离列**（DeepSeek 工具轮回放必需） |
| 内部调用流式空闲超时 | `llm_manager.chat_with_fallback(stream=True)` + `agent/llm/stream_aggregate.py` | 内部辅助调用（折叠/压缩摘要）可切流式通道：**每 chunk 独立空闲超时**（思考/输出增量都算活动），完全静默才判死；deadline 仅在尝试开始前/重试决策时检查。流式失败同样进错误分类/退避/回退链；聚合含 TTFT 与 usage |

## 用量与观测

- **用量归属**（`agent/mind/scope_usage.py`）：per-scope 累计 LLM 用量与 turns（`scope_usage` 表
  增量累加），`GET /api/status/usage` 查询。①委托链经 ContextVar 绑定父会话 scope，子代理
  reflect 的用量归属父会话；②`reflect:{uuid}` 一次性 scope 不建统计行（防孤儿行挤爆容量上限后
  新会话用量被静默丢弃）；③scope 解析链：anything.entity_scope > usage_scope 绑定 > 激活上下文
- **记账口径归一**：提取层按 `usage_prompt_includes_cache` 判定 prompt 是否含缓存（details 包装/
  DeepSeek 命中字段=含；仅原生 Anthropic 字段=不含），命中率一律 `cache_read / total_input_tokens`，
  scope_usage 累计前补回缓存量，输出 `prompt_miss_tokens = prompt - cache_read` 在两种口径下均成立
- **流式 usage 旁路全字段优先**（`response_parsing.install_usage_tap`/`_merge_sink`）：litellm
  对未收录模型的流式 chunk 会用本地 tiktoken 估算**伪造 usage**（prompt 虚高 ~1.8 倍、completion
  清零、缓存 details 丢弃）；旁路捕获原始 chunk 全量字段，见过原始 usage 即全字段以其为准。
  非流式与 native Responses 路径 usage 透传正常
- **占用锚点归一**：上下文占用（压缩触发锚 / token 预算提醒 / usage_percent）一律用归一化
  `total_input_tokens`（+completion）——独占口径（原生 Anthropic）下原始 prompt_tokens 不含缓存
  读写，以其为锚会低估占用、压缩触发偏晚；think_loop 状态字段收敛为单一 `last_input_tokens`
- **记账单一权威**：`chat_with_fallback(record_usage=False)` 供主对话路径关闭管理器侧记账——
  主对话每次调用在缓存命中统计与 scope 成本账本双写是缺陷；内部辅助调用（guardian/summarize 等）
  默认仍由管理器记账
- **Responses 路径** `cache_observable` 动态判定（无缓存字段=不可观测，不谎报可测 0%）；
  context_usage 事件键统一 `cache_creation_input_tokens`
- **TTFT 首 token 计时**：`ChatResult.ttft_ms` + EVENT_THINKING_LLM_END——流式路径记首 delta
  到达时刻，与 duration_ms 相减即输出生成耗时（「排队慢」与「生成长」分别可诊断）；非流式为 None

## 内部辅助调用

| 机制 | 位置 | 说明 |
|------|------|------|
| 摘要专用模型与思考档 | `mind.summarize_text` + `conversation_summary_model` / `conversation_summary_reasoning_effort`（cache/prompt 组） | 折叠/压缩摘要可指定更轻量模型与低思考档：模型经 `get_enabled_client` 解析（不存在/停用 WARNING 回落默认），effort 走 per-call options（调用方 > 模型配置；不支持思考自动忽略），失败走默认回退链韧性不降级。compressor 前缀复用路径刻意不动（KV 命中是其核心设计） |
| 压缩调用免尾锚点 | `prompt_cache.decorate_messages(tail_anchor=)` / `_invoke_llm_unified(cache_tail_anchor=)` | 一次性调用（前缀+摘要指令不会再被原样重发）省略链尾增量断点——该断点写入的缓存条目永无读者（纯写入费）；层锚点保留照常吃缓存读。默认 True 不影响 reply/reflect 的增量缓存设计 |
| light_llm 通道 | `agent/memory/dedup.light_llm` 等 | 内部小任务（判重合成/反思晋升/反馈分类/标签归并提示）统一走轻量通道，可经 `memory_light_model` 等配置指定专用模型 |
