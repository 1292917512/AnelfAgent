# 上下文缓存与用量排查

本文只在修改上下文组装、前缀缓存、用量统计或压缩逻辑时阅读。它描述排查顺序与红线，不记录某次
供应商事故。LLM 层机制详见[模型分册](llm-and-models.md)。

## 先区分三层问题

LLM 前缀缓存命中率是本项目的核心成本/性能指标。缓存工程分三层责任，排查时**先定位层再下结论**，
不要默认「缓存崩了」：

1. **客户端字节稳定性**（完全可控）：变动率排序组装 + tools 冻结 + 摘要窗口 + 单一装饰点。验证 =
   快照 section 哈希 diff + **PrefixGuard 运行时哈希链**（records.jsonl 的 `prefix_drift` 字段定位
   首个断裂消息）
2. **供应商缓存行为**（不可控）：磁盘缓存传播延迟/驱逐/节点亲和。判读特征 = prefix_stable=True 而
   read 浮动、1~2 轮自愈（「平台波动」）。**合法断裂单独标识**：折叠/压缩是已知的前缀整体重写，
   完成点经 `prefix_guard.note_legal_break(scope, reason)` 登记（fold/compress）——清空该 scope
   全部基线，并在 120s 窗口内让快照记录携带 `legal_break`，与「平台波动」区分
3. **统计与展示口径**：kind 分桶 / age_sec 回声 / unobservable / 单次钳制率平均；缓存命中率使用
   归一化的 `cache_read / total_input_tokens`，不把不同供应商是否把缓存读写计入 prompt 的口径混算

## 上下文层红线清单

改动记忆/召回/画像注入时逐条自查（前四条有不变量测试锁定：
`tests/unit/agent/mind/test_cache_layer_invariants.py`）：

1. **vol ≤ 30 禁入**：记忆召回/画像/关系/技能/状态/短期记忆内容块的 volatility 必须 > VOL_HISTORY(30)
   （stable/summary/conversation 是缓存前缀，一个字节变化即断裂）。新增 `@context_block` 时先想
   「这块多久变一次」
2. **pin 块独立成消息**：永久记忆块与召回/检索块必须分消息返回（`_format_unified_results`），
   recollection 的 startswith 提升只捕获纯永久块；合并成一条会把每轮变化的召回内容带进 context 层
3. **时间戳只准日期粒度**：召回渲染用 `%m-%d`/`%Y-%m-%d`（`_format_memory_time`）；秒级/计数器类
   易变字段不得进入任何注入块（状态计数器隔离在 status 层是刻意设计）
4. **fail-open 不注入错误文案**：召回/画像/关系/技能匹配任一异常 → 该块为空（管线跳过空内容），
   禁止把异常文本写进上下文——错误文案每轮不同，等效于注入易变内容
5. **session_token 暗坑**：`security_session_token_enabled` 开启后历史消息逐条包裹每轮随机的令牌，
   conversation 层字节全变、历史锚点恒失效——排查命中率时先确认该开关状态
6. **legacy 布局暗坑**：`context_tail_injection_enabled=false` 时动态块移到历史之前，召回结果直接
   击穿历史前缀——缓存友好布局依赖 tail injection 保持开启
7. **PreCompact flush 只写 DB**：压缩前的记忆抢跑提取（`_precompact_flush`）只写记忆库，不触碰任何
   prompt 分层内容；压缩本身的 invalidate+prewarm 走既有机制，记忆侧改动不得在这条路径上新增
   prompt 层写入

## 用量与版本变更

修改 litellm 或用量解析时，先确认 `pyproject.toml` 与 `uv.lock` 的实际版本，再运行针对性缓存
集成测试：

```powershell
LLM_CACHE_E2E=1 uv run pytest tests/integration/test_llm_cache_hit_e2e.py
```

可用 `LLM_CACHE_E2E_MODELS=a,b` 缩小模型集合。litellm 的流式 usage 处理是脆弱契约（历史版本曾对
未收录模型用 tiktoken 估算伪造 usage：prompt 虚高 ~1.8 倍、completion 清零、details 丢弃），因此：

- pyproject 精确锁定版本（`litellm==x.y.z`，禁止范围符）
- **litellm 升级同步规范（机械门禁）**：版本变动必须运行全模型缓存与用量健康门并全绿后才放行——
  对配置内全部启用 chat 模型经真实 LLMClient 管线发两次同前缀流式调用，断言：两次 usage 均回报 /
  completion>0（伪造指纹）/ 含缓存口径 read+creation ≤ prompt（尺度混血指纹）/ 可观测时第二次
  cached>0 且命中 ≥0.7（判别区间：伪造尺度混血 ≈0.55、平台粒度损失实测下限 ≈0.78；暖调用按
  2/4/8s 递增窗口吸收供应商写读传播滞后）
- litellm 升级前必须验证 `litellm.ModelResponse()` 可实例化；1.96 起 `AsyncHTTPHandler.client` 变为
  已关闭即惰性重建的属性（取引用再判 `is_closed`，重复访问会拿到新池）

上下文占用、压缩触发和预算提醒使用归一化输入量（含供应商缓存读写的统一口径）。主对话经过统一
调用入口时只记一次用量（`chat_with_fallback(record_usage=False)`），委托调用的成本归属父会话；
一次性反思 scope 不产生孤儿统计行。口径细节见[模型分册·用量与观测](llm-and-models.md)。

## 修改后检查

1. 对比 stable、summary、conversation、context 和动态尾部的消息快照
2. 检查 `_layer`、`_source` 是否只存在于内部消息，发送前是否经过 `normalize_for_send`
3. 先运行缓存不变量单测，再按模型和供应商需要运行集成测试
4. 将折叠、压缩、供应商不可观测和真实缓存波动分别标记，不能用「重启后变冷」推断缓存已清空
