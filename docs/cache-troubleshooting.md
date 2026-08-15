# 缓存命中率排查手册（ZCode 排障）

> 本文档是 AGENTS.md《缓存命中率排查手册》的详细展开。AGENTS.md 保留三层责任模型摘要与记忆系统红线清单（高频自查）；本手册承载完整诊断决策树、PrefixGuard、断点预算、压缩前缀复用与 e2e 回归等深度内容。

LLM 前缀缓存命中率是本项目的核心成本/性能指标。缓存工程分三层责任，排查时**先定位层再下结论**，不要默认"缓存崩了"：

**三层责任模型**：
1. **客户端字节稳定性**（完全可控，已接近极限）：变动率排序组装 + tools 冻结 + 摘要窗口 + 单一装饰点。验证手段 = 快照 section 哈希 diff + **PrefixGuard 运行时哈希链**（见下）。
2. **供应商缓存行为**（不可控）：DeepSeek 磁盘缓存的写入→可读传播延迟、条目驱逐、节点亲和。**判读特征：prefix_stable=True（前缀字节完全未变）而 read 上下浮动，1~2 轮自愈**——客户端无能为力，列表已以"平台波动"徽标自动标识，不计为故障。
3. **统计与展示口径**：kind 分桶 / age_sec 回声 / unobservable / 均值按单次钳制率平均（防 Anthropic 口径 read>prompt 放大超 100%）。

## PrefixGuard 前缀稳定性运行时守卫

`agent/mind/prefix_guard.py`（对齐 dsh agent-loop/invariant 的运行时不变式思想、适配为轻量观测版）：每次 LLM 调用前（`_invoke_llm_unified` normalize 前、`_layer` 尚存时）对守卫层消息逐条哈希（复用 `PromptCacheManager.compute_hash`），与同 (scope, kind) 上一次调用的哈希链比对，**首个不一致位置即缓存断裂点**（比快照层聚合 sha1 更细——能定位到层内第几条消息变了）。基线按 (scope, kind) 分键隔离前缀族，conversation 纯追加免疫（只报既有位置改动）。**仅观测不阻断**（fail-open），归因写入 records.jsonl 的 `prefix_drift` 字段（`{broken_at_index, layer, prev_hash, cur_hash}`；链收缩时 `reason=guarded_chain_shrunk`）。守卫层由 `prefix_guard_layers` 配置（逗号分隔，默认 `stable,summary,conversation`）。判读：`prefix_drift` 非空即该次调用前缀断裂，`layer` 指认漂移层；压缩/折叠/人设切换是合法断裂源（可 `prefix_guard.reset(scope)` 清基线避免误报）。

## 诊断决策树（对任意一次低命中）

- 前缀稳定（快照 sections 除 tool_chain/exec_context 均未变）+ 命中低 ⇒ **平台波动**，看下一轮是否自愈，是则结案
- 某前缀 section 变更 ⇒ 首个变更层即断点位置，查该层写入方（便签=心跳任务/技能评审；summary=折叠；tools=激活跳变）
- kind=reflect 且首轮 ~90% 以下 ⇒ 任务结构下限（lean 模式下仅剩任务指令差异）
- 重启后首轮低 ⇒ 冷启动（布局变更会使旧缓存条目按新层序失效，一次性）

## 数据来源（按可信度排序）

- `logs/context_snapshots/records.jsonl`：每次 LLM 调用的紧凑缓存记录（prompt/read/creation/hit_rate/kind/model/age_sec/unobservable/**prefix_drift**）。连续捕获模式下逐条写入。`prefix_drift` 为 PrefixGuard 断裂归因（null=前缀稳定），是命中率下跌时定位"哪层哪条消息漂移"的第一手证据。
- 应用日志 `LLM 用量: prompt=... cache_read=...`（DEBUG，tag 思维）：单调用 ground truth。
- Web 快照缓存面板（Context→快照）：`last_call`（主对话口径）/ `recent` / `recent_all`。

## 判读规则（避免误读）

- `kind` 分桶：主对话 `reply` 与辅助 `reflect`（评审/心跳/折叠）分开。辅助调用无共享前缀，命中率 0 属正常，**不计入主口径**。
- `age_sec`：`last_call` 可能回显更早会话的调用（回声）。>120s 视为过期，前端置灰标"（N 分钟前）"，不代表当前缓存失效。
- `unobservable`：该模型流式 usage 缺缓存字段（见下表），缓存仍在服务端生效但**无法度量**，显示"—"而非 0%。
- `expected_prefix_tokens`（快照 cache 区块）：断点锚点覆盖的字节稳定层（stable+context+summary）理论可命中前缀。**与实际 cache_read 对比是首要判读工具**：expected 高而 read=0 ⇒ 前缀内容没变、问题在网关侧（节点亲和/TTL/端点行为）；expected 本身下降 ⇒ 前缀内容漂移，去查 section diff。
- 单次 0% 的四大可解释原因：① 重启/空闲 >5 分钟的冷启动；② 工具集激活跳变（tools 数组变化击穿前缀）；③ 折叠/评审等辅助调用；④ **节点亲和失配**（kimi coding 等网关：缓存节点本地 + TCP 连接亲和，并发迫使新连接 = 冷节点全量 miss——已由 `_CacheAffinityHTTPHandler` 小连接池收敛，`anthropic_cache_pool_size` 配置池大小，默认 4；仍出现成簇 0% 且排除①②③时怀疑此项）。稳态主对话应 90%+。
- **每个新回复第 1 轮恒定低位平台（轮 2 起恢复）= 历史前缀在固定位置断裂**：查快照 section diff 从上往下定位首个变更层。已根治的两类：召回内容混入 context 层（永久块与召回合并成一条被 startswith 提升——必须分消息返回）；**折叠死态**（调度判定误用截断后行数，积压超宽限即永不调度 → 窗口逐条滑动 → conversation 层每回复都变；判读特征：`conversation_summary` 表 folded_count 长期停滞 + dropped_count 不增 + 无任何折叠日志）。
- **任务/reflect 调用首轮 ~66% 是结构下限的判读**：任务每轮都写便签/文件使其漂移，环境注入块随任务上下文携带时首轮只能命中 tools+stable 共享头。已由 `task_lean_context`（默认开）根治：任务上下文精简为人设+工具+永久记忆+任务指令。快照/记录带 `kind`（reply/reflect），历史列表以 kind 徽标区分主对话与任务调用，任务行的低命中不再误读为主对话故障。
- **kimi coding 端点特性**（实测）：仅流式请求有缓存读写（非流式 usage 恒 read=0/creation=0，主对话全走流式不受影响，流式失败降级非流式的罕见路径会丢缓存）；缓存按 TCP 连接亲和。

## 供应商缓存字段

`agent/llm/types.py` 的 `_CACHE_*_PATHS` 注册表，新增供应商只登记字段路径：

- Anthropic：`cache_read_input_tokens` / `cache_creation_input_tokens`（需断点，发送边界 `decorate_messages` 注入 cache_control）。
- OpenAI 系：`prompt_tokens_details.cached_tokens` / `input_tokens_details.cached_tokens`。
- DeepSeek：`prompt_cache_hit_tokens`（磁盘缓存自动生效）。**litellm 流式 chunk 转换会丢弃供应商扩展 usage 字段**（SDK 层完好，1.95/1.96 均丢），由 `response_parsing.install_usage_tap` 旁路（包装 `completion_stream` 从原始 chunk 挖字段汇合）恢复可观测性。
- **可观测性是动态判定**（`UsageInfo.cache_observable` = 原始 usage 是否携带缓存字段，存在性与值无关）：字段在值为 0 = 真实未命中（显示 0%）；字段缺失 = 端点不回报（显示"—"，不计入均值）。不要再按供应商名静态登记。

## 架构不变量（改动时勿破坏）

上下文按变动率排序组装（`agent/mind/context_pipeline.py` 的 `@context_block` 声明），stable→summary→conversation→context(便签)→动态区→exec_context；tools 数组是 prompt 最大头且需跨会话字节稳定——三层保障：确定性双桶排序（`tool_order_deterministic`，作用域工具沉尾，跨 scope 共享头部）→ 进程内粘性（`tool_dynamic_sticky`）→ **跨回复追加式冻结**（`tool_order_frozen`，ToolAssembly 持有冻结名单：新工具只追加尾部、热召回换血/来源成员变化不剔除，数组只增不改）；**工具可见性与权限分离**（reflect/受限角色不从数组裁剪 schema——裁剪会在 tools 前缀早期位置断裂跨模式共享缓存——禁用工具由 think_loop `blocked_tools` 执行侧拦截返回合成错误）；stable 层不得嵌入动态状态（默认模型标记、视觉文案已移出）；**context 层（便签/文件索引/纯永久记忆块）放尾部动态区最前，不得移回前缀锚点位**——心跳任务/技能评审/记忆写入都会改便签，在前缀时每条漂移作废其后 20-40K 历史缓存（空闲后首轮跌到稳定层量级 ~30% 的判读特征）；召回/检索结果每条消息都变，必须与永久块分消息返回（memory_retriever._format_unified_results），recollection 的 startswith 提升只捕获纯永久块，召回留在尾部动态区。

## Anthropic 断点预算

每请求 ≤4，设施集中在 `agent/llm/prompt_cache.py`（借鉴 Hermes prompt_caching.py）：**唯一装饰点是发送边界的 `decorate_messages`**（llm_invoker 在 normalize 前、`_layer` 标签尚存时调用），按声明式锚点表放置：stable 层末 + 对话历史末（无历史回退摘要块）+ 链尾（无 `_layer` 的末消息，天然随工具链增长前移）；便签层在尾部动态区不打锚点（漂移内容占断点既浪费预算又固化易变字节）；管线/think_loop/内容构建侧只打 `_layer` 标签，**谁都不写 cache_control**。wire `tools[-1]` 断点是传输层权威（llm_client 按消息侧计数门控补位，满 4 让位）。装饰全部 copy-on-write，共享上下文字典永不被改写。TTL 由 `prompt_cache_anthropic_ttl`（5m/1h）驱动；`prompt_cache_tools_breakpoint` 控制 tools 断点。

## 跨供应商回退

cache_control 是 Anthropic 专属字段。`chat_with_fallback` 回退到非 Anthropic 候选时经 `strip_cache_control_copy` 发剥离副本（原列表与 think_loop 共享，禁止原地剥离）；回退到 Anthropic 候选则原样保留。1h TTL 时 llm_client 自动携带 `extended-cache-ttl` beta 头（官方端点缺头 400）。

## 压缩摘要前缀复用

`context_compressor._summarize_with_prefix`（对齐 dsh compaction-basic/summarizer 的 COMPACTION_INSTRUCTION 设计）：上下文压缩的摘要调用不再发独立的文本渲染请求（与主前缀零共享、全价计费），而是把 `head_system + head + middle`（= `base_messages + tool_chain` 截去保尾段的**字节级前缀**，亦即上一次真实请求的前缀）原样作为消息前缀，摘要指令作为末条 user 消息追加，经 `_invoke_llm_unified`（`purpose="compress"` 分桶、`tool_choice="none"` 禁止工具调用）发出——辅助调用成为上一次请求的前缀扩展，命中 KV 缓存。**tools 数组随前缀传入**（`round_helpers._compress_context` 透传 `ctx.active_tools`，缺它前缀从第 0 字节就不匹配）。`_extract_previous_summary`/`_extract_user_messages` 只做视图过滤不改前缀字节，故前缀在 extraction 前捕获即正确。失败逐级回退：前缀路径 → 文本渲染路径（`summarize_text`）→ 确定性摘要。配置 `compression_prefix_reuse`（默认 True）可整体回退旧行为。注意：conversation_fold 的折叠摘要处理窗口外旧消息（不在当前请求前缀内），**不复用前缀**，仍走 `summarize_text`。

## 缓存命中 e2e 回归

`tests/integration/test_llm_cache_hit_e2e.py`（env-gated 默认 skip）：`LLM_CACHE_E2E=1` + `ANTHROPIC_API_KEY`/`DEEPSEEK_API_KEY` 时，同一前缀连续两次 `chat_stream`（max_tokens=1），断言第二次 `cache_read_input_tokens > 0`——把"前缀字节稳定性"的最终证明锁进回归门（对齐 dsh request-cache.e2e.ts）。`cache_observable=False` 的端点自动 skip（不误报）。
