# 模型与 LLM 层

## 模块模型 SDK

实体与组件从 `entities._sdk` 导入 `get_model_api`，频道从 `agent.channel` 导入同名接口。
两者使用同一个进程内 `ModelAPI`，直接复用项目模型配置；不访问本机 Web API，不自行复制供应商
密钥、客户端或回退循环。`get_llm_manager` 留给模型配置管理，普通调用使用此 SDK。

```python
from entities._sdk import NoulQuestion, get_model_api
# 频道使用：from agent.channel import NoulQuestion, get_model_api

models = get_model_api()
reply = await models.chat(
    [{"role": "user", "content": "概括这段文字"}],
    purpose="my_component.summary",
)
text = reply.content
assessment = await models.judge(
    {"message": text},
    {"relevant": NoulQuestion(instructions="这段内容是否与当前任务有关？")},
    native_only=True,
)
audio = await models.text_to_speech(text, voice="configured-voice")
audio_bytes = audio.value
```

- `chat` 支持工具 schema、多模态消息、生成参数和 `stream=True` 流式聚合，复用已有重试、协议路由
  与用量记账；只返回 `ChatResult`，不会执行返回的工具调用、发送频道消息或写入会话历史。
- `describe_images` / `describe_video`、`embed` / `embed_multimodal`、`transcribe` /
  `text_to_speech`、`rerank`、`generate_image` / `edit_image`、`generate_video`、`generate_music`
  覆盖模型配置中的能力类型。结果为 `ModelResult(value, model_id, model)`，携带真实模型归属。
- 不传 `model_id` 时，逐次读取对应类型的优先级，跳过停用或能力不匹配的模型，失败尝试下一候选。
  显式传入配置 ID 时固定该模型；不存在、停用或类型不符直接报错，不隐式换供应商。
  持久化向量索引必须固定嵌入模型，不同模型即使维度相同也不能混用向量空间。
- 媒体调用继承所选模型的凭据、自定义请求头、协议和代理配置，每个候选只尝试一次；
  `ModelCallError.errors` 提供按尝试顺序排列的脱敏错误。取消立即传播，不触发下一候选。
  可选 `timeout` 限制每个媒体候选的完整操作；不设置时沿用客户端自身的请求、流式空闲或任务轮询时限。
- `rerank` 校验候选索引唯一且属于本次请求、评分为有限数值、结果数满足请求上限。
  空响应、缺项、重复或越界按调用失败处理，进入既有回退逻辑；不把相关性评分当作已校准概率。
- `judge` 接受显式状态和类型化问题，复用判断引擎的候选排序、离线跳过与恢复。
  `native_only=True` 禁止普通聊天模型回退；不设置时遵循判断配置。SDK 不自动注入当前会话，
  调用方按任务提供必要上下文，不跨会话读取隐含状态。判断答案的否定不是故障，不会因答案为否而重试。
- `list_models("vision")` 等接口仅列出按优先级排列的已启用配置 ID，不暴露密钥或发起联网检查。

模型配置管理、调用协议、韧性重试、用量口径与内部辅助调用的现状说明。修改 `agent/llm/`、
`entities/model_control/` 或用量统计时按需阅读；缓存排查见[上下文缓存分册](context-cache.md)。

## 可选工作的原生预判

`agent.judgment.gate.assess_optional_work` 用 Jev 判断自动深探与技能评审是否值得执行。
仅在原生判断已启用并配置服务时调用（本地服务可以无密钥）；未配置、停用、超时、无效答案或不确定时保留原流程，
此入口不走普通聊天模型回退。同一次请求并行回答 Noul 必要性与 Choice 依据类别，共享事实输入。
只有必要概率不高于 `judgment_gate_skip_threshold`（默认 0.1），且「已覆盖／不相关」
依据概率不低于 1−该阈值，才跳过。答案缺失、矛盾或不确定时继续；不把多个概率相乘推算可靠性。
`judgment_gate_timeout` 默认 5 秒，是整体等待预算；`judgment_gate_enabled` 可关闭这层优化，
配置均由 `judgment/core` 配置面供 AI/Web 编辑。

对话补充回忆传入 `candidates` 时改为一次请求内逐条 Choice 判断，只接纳明确有用且选中项概率
达到 `judgment_recall_confidence` 的候选（默认 0.8）。缺失、矛盾或不确定的候选不自动注入；
服务不可用则由调用方沿用本地匹配结果。此阈值不改变首轮深探、技能评审或回复投递规则。
候选保留归属与日期，排除工具回执、模型独白和系统提示；判断结果仅存在钩子观测面。

输入是有界对话事实、会话 scope、已召回内容及其归属标签或技能库状态。
`judgment_evidence_max_chars` 默认 3000，是整个会话证据的共享预算（含 JSON 转义）；
优先保留最新用户请求，补入近期执行事实，保留身份标签、时间顺序与截断标记。
系统提示与动态注入不冒充用户输入，多媒体只提取文本块，工具调用只取名称与参数摘录。
自动回复审核使用同一窗口，已发送回复通过 `delivered` 标记，不再另复制正文；待审正文保持完整。
`judgment_auto_max_chars` 默认 8000，约束自动判断的整体事实输入，超限不调用模型：
可选工作继续原流程，自动回复按投递开关正常发送。正文不会被截断后当成已完成审核；
自动审核仅在完整、有效且一致的高置信拒绝时拦截，信息不足、超时或不可用均保留默认投递。
两项配置都可经 AI/Web 热调，不限制显式 `judge` 或 SDK 输入，也不改变完整技能评审的继承上下文。
预判不修改主模型上下文，只决定是否启动可选工作，不替代技能写入决策、手动召回或记忆内容判断。

工作区与钩子页用独立子节点保留结论、依据类别、概率、模型、实际 token 用量与单次耗时；
后续阶段不覆盖判断记录。每次钩子最多保留 8 条判断，随现有进程内历史回收，不落盘保存输入。
后台任务进度同样携带有界判断摘要，AI 可按需查询，不改写稳定缓存前缀。

`judgment_slow_seconds` 默认 3 秒，达到配置值才提示偏慢，独立于请求超时预算。
实际预判在工作区与日志中标注端到端耗时（包括网络），超时继续原流程。模型页「判断 (Jev)」
的「检查连通性」仅 HEAD，零推理；「判断测速」明确提交三题样例并消耗额度。
已选服务的测试不以普通模型或其他原生模型回退掩盖 Jev 故障，
同时显示偏慢警告和是否超出预判预算。单次小样例不代表长输入或高并发下的服务性能。

设计依据：[并行问题](https://docs.typesafe.ai/patterns/fan-out)与
[模型已知边界](https://docs.typesafe.ai/model-jaggedness/jev-1.13)。窄判断交给 Jev，
算术、期限、调度和权限留在确定性代码；目前不以 Jev 替换复杂检索规划，也不增加每轮无条件判断。

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

Responses 返回 `reasoning` item 时，当前执行链保留同一响应的全部原生 `output` item，下一轮调用
按原序回放，再追加工具结果；这样 `function_call` 的 `id` 与 `call_id` 均维持端点原值。流式路径
从完成事件的 `output` 取终态项，不使用未完成的增量项。普通 chat_completions 请求在 LLM 边界
剥离该原生字段；执行链完成后也不写入持久会话历史。

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
| 摘要专用模型与思考档 | `ModelInvoker.summarize_text` + `conversation_summary_model` / `conversation_summary_reasoning_effort`（cache/prompt 组） | 折叠/压缩摘要可指定更轻量模型与低思考档：模型经 `get_enabled_client` 解析（不存在/停用 WARNING 回落默认），effort 走 per-call options（调用方 > 模型配置；不支持思考自动忽略），失败走默认回退链韧性不降级。compressor 前缀复用路径刻意不动（KV 命中是其核心设计） |
| 压缩调用免尾锚点 | `prompt_cache.decorate_messages(tail_anchor=)` / `ModelInvoker.invoke(cache_tail_anchor=)` | 一次性调用（前缀+摘要指令不会再被原样重发）省略链尾增量断点——该断点写入的缓存条目永无读者（纯写入费）；层锚点保留照常吃缓存读。默认 True 不影响 reply/reflect 的增量缓存设计 |
| light_llm 通道 | `agent/memory/dedup.light_llm` 等 | 内部小任务（判重合成/反思晋升/反馈分类/标签归并提示）统一走轻量通道，可经 `memory_light_model` 等配置指定专用模型 |
