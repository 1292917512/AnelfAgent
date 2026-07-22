# P4 子计划：回归验证与打磨

> 贯穿 P1–P3，每个阶段完成后执行本节对应部分；全部完成后做总回归。

## 1. Anelf 优势保留验证清单（每阶段后逐项确认）

| # | 优势 | 验证方式 |
|---|---|---|
| 1 | 实体注册 + 两级能力发现 | `tests/core`、`query_entities` 工具手工验证 |
| 2 | 标签驱动工具注入 | PFC 工具选择单测 + 媒体消息触发 media:image 工具激活 |
| 3 | Mind 双层决策 + 纯工具模式 | 元决策单测；确认回复仍走 send_message/end_reply |
| 4 | 多通道 + 看门狗 + 通道化审批 | cli/webui 通道冒烟；审批消息在通道正常渲染 |
| 5 | 混合记忆 + Cognee + 笔记蒸馏 | `tests/agent/memory` 全绿；recall 手工验证 |
| 6 | 心跳/规划/技能/委派 | `tests/agent/{heartbeat,planning,skills,delegation}` 全绿 |
| 7 | 会话 token 防注入 + 威胁扫描 | `tests/agent/security` 全绿 |
| 8 | 三层 prompt 缓存 | 对比改动前后 stable 层 hash 命中率（context_audit 日志） |
| 9 | UI 反向驱动（ui_*） | ui_notify/ui_ask/ui_compose 手工验证 |
| 10 | 多模型 fallback + 轮中消息合并 | `tests/agent/llm` 全绿；流式改造后 fallback 仍生效 |

## 2. 全量测试

- [ ] `pytest tests/` 全绿（62+ 个测试文件，新增测试并入）
- [ ] 前端 `npm run build` + 类型检查通过
- [ ] 冒烟矩阵：cli 通道 / webui 通道 × 简单问答 / 文件编辑 / shell / 审批 / 长对话压缩

## 3. 性能与缓存审计

- [ ] 流式切换后 prompt 缓存键不受影响（stable 层内容不变）
- [ ] token 消耗对比：同任务改动前后 API 用量（microcompact + persisted-output 应显著降低长任务消耗）
- [ ] SSE 事件频率评估：delta 事件节流（如 50ms 合帧）防前端渲染过载

## 4. 文档收尾

- [ ] 更新 `04-master-plan.md` 对照矩阵状态列（✅ 已完成 / ⏭️ 跳过及原因）
- [ ] AGENTS.md / README 涉及工具清单的章节同步
- [ ] 每个移植点注明出处（Claude Code 文件:行号），便于后续溯源
