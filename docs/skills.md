# 技能系统

`agent/skills/` 的现状说明（catalog 可见层 + skill_index 事实层 + skill_matcher 放大层 + tools
决策协议）。技能文件存储在 `workspace/skills/`。事实归系统、决策归 AI 是贯穿原则。

## 后台评审预判

技能评审通过现有操作事实门槛后，在候选检索和完整评审前使用
[Jev 可选工作预判](llm-and-models.md#可选工作的原生预判)。只有明确判断无需评审时才跳过；
未配置或不可用时继续。预判遵守钩子的上下文继承档位，不读取被关闭的 transcript。
继续评审时保留原始完整消息链与工具目录，评审指令仍只追加尾部，缓存前缀不变。

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

自动技能匹配只在首轮准备阶段执行，执行中不根据工具结果另起后台匹配或判断。
候选不表示执行或已读正文；模型仍可通过目录和技能工具主动发现、读取技能。
`[skill:名称]` 可在工作区打开详情，并由出站清洗剥离。
共享查询向量失败或被独立取消时保留关键词路，不重复请求；会话取消仍向上传递。
向量等待结束后重新读取技能定义，避免把期间删除或修改的旧技能作为候选。

## 本地 MCP 关联

技能页的「关联 MCP」与 AI 的 `set_skill_mcp_servers(name, server_names)` 共用技能层操作，
传完整服务名数组，空数组清除；最多 16 个。关系只保存到 `.meta.json`，不写入 `SKILL.md`，
不改变标准技能源、导入导出或正文向量。`dependencies` 仍是技能作者可分发的依赖声明，
本地关联使用 MCP 的确切配置名称，不按插件后缀推断。

首轮候选、对话补充候选和 `get_skill` 提供启用、连接及当前会话的工具分组激活状态。
关联不连接、不安装、不激活 MCP；AI 通过现有 MCP 管理和 `activate_tool_group` 决定下一步。
服务删除后保留关联并标为未配置，便于修复；状态源不可用则标为未知，不冒充未安装。
状态通过运行时组合根注入只读查询接口，技能模块不导入 MCP 实体或思维工具激活实现。

Web 通过 `GET/PUT /api/skills/{name}/mcp` 查看和保存；未指定会话的界面显示「按会话唤醒」，
不把其他会话的激活状态混入。工作区的紧凑 MCP 标记可打开状态快照，观测元数据不发送给模型。
状态只在动态技能块和工具回执中出现，不更新稳定目录或前缀，也不额外生成通知。

## 技能召回

系统按对话匹配候选摘要，AI 自主决定是否用 `get_skill` 读取正文及执行技能。
普通对话的技能匹配不受记忆档位限制；深度档复用记忆检索计划，其余档位直接匹配对话。
技能开关与内部任务精简模式仍可跳过自动匹配，技能工具保持独立。

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
  （仍被检索到不归档）；use/match 信号分离（检索注入不刷活动时间，get_skill 计使用并刷新活动）
- **恢复通道**：`restore_skill` 工具 + `SkillStore.restore()`（置 ACTIVE 并刷新活动时钟，防重力
  立即打回）；merge_skills 可逆合并（源 ARCHIVED 带 merged_into）
- **外部技能源**（sources/）：SkillSource 抽象 + 注册表热插拔（内置 SkillHub 源，删模块即卸载）

## 执行观测与技能库

- 工作区按执行轮次展示两种事实：**召回候选**是匹配后进入上下文的技能摘要，
  **已读取正文**是成功的 `get_skill` 调用。
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
- 异步嵌入回写前核对模型和当前技能表征；修改、删除、切换模型后的迟到结果不回写。
  写入诊断中的未提交草稿仅供比较，不覆盖库内定义的持久化向量。
- 会话匹配只读取已有技能向量，缺失时保留关键词路并唤醒 EmbeddingWorker；
  启动装配即注册技能 backlog，空闲时分批补齐，不等待首次会话或心跳。
  主动诊断仍支持预算化补算（`skills_embed_budget`，advisory 收紧 8）；
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
| `agent/skills/runtime.py` | 共享技能运行依赖 |
| `agent/skills/curator.py` | 技能策展（重力 + 议程） |
| `agent/skills/background_review.py` | 技能后台评审（钩子面承载） |
