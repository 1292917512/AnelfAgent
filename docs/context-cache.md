# 上下文缓存与用量排查

本文只在修改上下文组装、前缀缓存、用量统计或压缩逻辑时阅读。它描述排查顺序，不记录某次供应商事故。

## 先区分三层问题

1. **客户端字节稳定性**：检查 `ContextPipeline` 的层排序、工具数组冻结、摘要窗口和发送边界装饰。用 `PrefixGuard` 的记录定位首个 `prefix_drift`，不要先把供应商行为当成代码回归。
2. **供应商缓存行为**：客户端前缀稳定时，缓存读数仍可能受传播延迟、驱逐和节点亲和影响。`prefix_stable=true` 而 read 波动通常属于这一层；折叠和压缩会合法地重写整个前缀，应通过 `prefix_guard.note_legal_break(scope, reason)` 登记。
3. **统计口径**：区分 `cold`、`warm`、`unknown` 和不可观测数据；缓存命中率使用归一化的 `cache_read / total_input_tokens`，不要把不同供应商是否把缓存读写计入 prompt 的口径混算。

## 上下文层红线

- 记忆、画像、关系、技能、状态和短期记忆不得进入 `stable`、`summary` 或 `conversation` 的低变动层；新增 `@context_block` 时先确定其实际变化频率。
- 永久记忆块和每轮召回块分消息返回，避免召回变化击穿永久前缀。
- 注入内容使用日期粒度时间；不要把秒级时间戳、计数器或每轮随机值放入稳定层。
- 召回、画像或技能匹配失败时返回空块并记录日志，不把异常文本写进上下文。
- 开启 `security_session_token_enabled` 会使历史消息包含每轮随机令牌，排查缓存时要把它作为已知的前缀破坏因素。
- 保持 `context_tail_injection_enabled` 的尾部布局；把动态块移到历史之前会直接破坏历史前缀稳定性。
- 压缩前的记忆提取只写数据库；压缩负责重建缓存基线，记忆写入不要额外改写 prompt 层。

## 用量和版本变更

修改 litellm 或用量解析时，先确认 `pyproject.toml` 与 `uv.lock` 的实际版本，再运行针对性缓存集成测试：

```powershell
LLM_CACHE_E2E=1 uv run pytest tests/integration/test_llm_cache_hit_e2e.py
```

可用 `LLM_CACHE_E2E_MODELS=a,b` 缩小模型集合。测试应确认流式调用能返回完整 usage、completion 不为零、缓存读写字段没有与 prompt 口径混合；供应商不可观测时应报告 `unknown`，不能伪造可测的 0%。

上下文占用、压缩触发和预算提醒使用归一化输入量（含供应商缓存读写的统一口径）。主对话经过统一调用入口时只记一次用量，委托调用的成本归属父会话；一次性反思 scope 不应产生孤儿统计行。

## 修改后检查

1. 对比 stable、summary、conversation、context 和动态尾部的消息快照。
2. 检查 `_layer`、`_source` 是否只存在于内部消息，发送前是否经过 `normalize_for_send`。
3. 先运行缓存不变量单测，再按模型和供应商需要运行集成测试。
4. 将折叠、压缩、供应商不可观测和真实缓存波动分别标记，不能用“重启后变冷”推断缓存已清空。