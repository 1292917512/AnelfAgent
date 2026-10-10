# 技能系统

`agent/skills/` 的现状说明（catalog 可见层 + skill_index 事实层 + skill_matcher 放大层 + tools
决策协议）。技能文件存储在 `workspace/skills/`。事实归系统、决策归 AI 是贯穿原则。

## 召回双层

- **目录（catalog.py）**：全部可用技能（active+stale，stale 行带「闲置」后缀）的 名称+单行描述
  进 stable 工具块，按 (created_at, name) 排序保持 append-only 字节稳定，预算
  `skills_catalog_max_chars`/`skills_catalog_desc_chars`（默认 16000/160，配置组 skills/catalog）
  逐级降级（半描述下限 48 → 纯名称+省略计数）——技能寻址不依赖匹配命中，描述长度是模型路由依据
- **匹配器（skill_matcher.py，放大层）**：匹配面 = 全量技能含归档（归档命中 ×`skills_match_archived_factor`
  降权、注入标注【已归档】并附 restore 指引；归档是库容卫生而非删除）。混合评分 = 关键词
  （trigger_patterns ×2 加成与技能名 kebab 分词 token 命中率取 max，名称 token 零维护自给）×
  `skills_match_keyword_weight` + 语义余弦互补权重，阈值 `skills_match_min_score`；多查询车道
  （对话尾部基查询 + 记忆召回规划产出的互补查询——经 `recall_split(plan_out=)` 复用同一 LLM
  检索规划）按技能取 max 合并，近重复折叠（≥`skills_match_redundancy`）并入合并信号；
  阈值/权重/系数/top_k 全在 `skills/match` 配置组

## /name 技能手势（gesture.py）

真实用户消息以 `/技能名` 开头 → 绕过语义评分确定性注入（防伪造：仅外部消息路径检测）；
手势三态——stale 照常注入、archived 自动 restore 后注入（显式点名是最强恢复信号）、不存在写
短期记忆提示模型回应。

## 写入决策协议

create/update 在事实层检测到显著信号时**不拒绝**，返回 needs_decision 诊断报告，AI 带 decision
回执重呼写入（rationale 落盘问责）或改走 merge/放弃：

- 语义相近 ≥ `skills_similar_threshold`（诊断比对含归档技能，facts 带 state，引导 restore+update
  而非重复新建）
- 触发词碰撞 ≥ `skills_trigger_collision_limit`
- 容量水位 / 无实质变化

评审上下文由 SkillIndex 供给（语义相近 top10 + 库健康摘要）。

## 后台评审与策展

- **后台评审**（background_review.py）：经 LLM 钩子面注册 `skill_review` 钩子（after_reply +
  transcript 档 + route_output=False + 6 轮上限）——评审材料为完整 transcript + 四问框架，
  工具结果细节不被摘要蒸馏丢弃；评审报告只留完成记录、不唤醒主回复周期
- **策展重力**（curator.py）：闲置降级/归档 + 试用期快筛（零参与 14 天降级）+ stale 软保留
  （仍被检索到不归档）；use/match 信号分离（检索注入不刷活动时间，get_skill 计数不刷活动）
- **恢复通道**：`restore_skill` 工具 + `SkillStore.restore()`（置 ACTIVE 并刷新活动时钟，防重力
  立即打回）；merge_skills 可逆合并（源 ARCHIVED 带 merged_into）
- **外部技能源**（sources/）：SkillSource 抽象 + 注册表热插拔（内置 SkillHub 源，删模块即卸载）

## 向量生命周期

- 覆盖口径 = 匹配面（全量技能含归档，归档向量不失养）
- 缓存键 = 模型名 + 文本 hash（模型切换即全库失效重嵌，防跨模型余弦混算）
- 交互路径预算化补算（`skills_embed_budget`，advisory 收紧 8），心跳 `warm()` 批量预热；
  死键清理时机 = 嵌入完成后（warm/embed_now）+ 删除时，列表重建不清理（防误杀待嵌入键）
- Web 经 `services._runtime` 拿 Mind 侧索引展示 embedded 状态与覆盖统计，CRUD 后 embed_now
  即时重嵌；Mind 构造时重绑定工具依赖避免双向量缓存
- 向量构建状态机（Web 可观测/可操作/可配置）：`build_state()` 暴露 idle/warming/rebuilding +
  进度；`skills_warm_batch_size` / `skills_rebuild_batch_size` 可调；`POST /skills/vectors/rebuild`
  手动触发重建（幂等）；每技能行内 `POST /skills/{name}/embed` 单技能重嵌
- **向量持久化**：`skill_vectors.sqlite3`（主库同目录独立文件，pack_embedding float32 BLOB）——
  嵌入即 upsert，首次访问懒加载恢复（模型+文本 hash 双因子校验，失配行清除并标记重建），
  **重启零重嵌**；模型切换内存与 DB 同步清空

## 关键文件

| 文件 | 职责 |
|------|------|
| `agent/skills/skill_store.py` | 技能存储（workspace/skills/SKILL.md；use/match 信号分离 + merge 可逆合并 + restore 恢复） |
| `agent/skills/skill_index.py` | 技能事实索引（向量/相似度/写入诊断/库健康快照/聚类——只产事实不做策略） |
| `agent/skills/skill_matcher.py` | 技能匹配（多查询车道 + 混合评分 + 近重复折叠） |
| `agent/skills/catalog.py` | 技能目录（append-only 字节稳定 + 预算逐级降级） |
| `agent/skills/gesture.py` | /name 技能手势 |
| `agent/skills/curator.py` | 技能策展（重力 + 议程） |
| `agent/skills/background_review.py` | 技能后台评审（钩子面承载） |
