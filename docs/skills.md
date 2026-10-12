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

自动评审仅统计本轮已返回结果的操作，不扫描历史调用。`skills_review_min_operations` 控制有效操作数门槛（默认 3，0 为每轮）；操作失败或读取技能可直接触发，控制与发送工具不计入门槛。门控只筛选材料，是否沉淀仍由 AI 判断；纯文本经验可直接通过技能工具维护。

- **后台评审**（background_review.py）：经 LLM 钩子面注册 `skill_review` 钩子（after_reply +
  transcript 档 + route_output=False + 6 轮上限）——评审材料为完整 transcript + 四问框架，
  工具结果细节不被摘要蒸馏丢弃；成功评审只留完成记录，失败经内部通知交给 AI 判断。带 per-scope 冷却
  （`llm_hook_skill_review_cooldown_seconds`，默认 120s），不限制执行总时长。
  上下文、模型和轮次预算统一在 `hooks_llm/skill_review` 配置；不继承对话时只检查技能库事实。
  transcript 保留完整消息、缓存分层及原工具顺序，补齐必需工具；模型或工具目录变化仍会影响缓存。
  执行进度进入动态上下文尾部，工作区可打开关联的模型、工具与结果详情。
- **策展重力**（curator.py）：闲置降级/归档 + 试用期快筛（零参与 14 天降级）+ stale 软保留
  （仍被检索到不归档）；use/match 信号分离（检索注入不刷活动时间，get_skill 与显式调用计使用并刷新活动）
- **恢复通道**：`restore_skill` 工具 + `SkillStore.restore()`（置 ACTIVE 并刷新活动时钟，防重力
  立即打回）；merge_skills 可逆合并（源 ARCHIVED 带 merged_into）
- **外部技能源**（sources/）：SkillSource 抽象 + 注册表热插拔（内置 SkillHub 源，删模块即卸载）

## 执行观测与技能库

- 工作区按执行轮次展示三种事实：**召回候选**是匹配后进入上下文的技能摘要，
  **显式调用**是用户手势提供的技能正文，**已读取正文**是成功的 `get_skill` 调用。
  读取不代表技能内各步骤已经完成；失败和仍在执行的读取不会标为已读取。
- 技能名称、来源、匹配分及截断情况经消息 `_source` 携带，在最终上下文预算之后提取。
  预算外技能不显示为已注入；观测数据发送给供应商前剥离，不增加 token 或改变缓存前缀。
- 技能页复用工作区有界内存记录，按频道与执行轮次展示近期参与。进程重启后清空，
  不增加历史数据库。点击技能可直达正文，列表支持库状态筛选、匹配次数与读取次数排序。
  库内「可用 / 闲置 / 归档」与某轮是否使用独立；向量维护归入检索索引与库健康区域。
  每轮摘要最多保留 32 个技能，独立于过程正文淘汰；达到上限会显示记录截断提示。

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
