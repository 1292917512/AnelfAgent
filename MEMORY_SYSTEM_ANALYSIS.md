# 记忆系统深度分析与优化方案

> 分析日期：2026-03-15
> 范围：`config/memory/`、`agent/core/mind/memory/`、`agent/core/mind/introspection/`、`config/introspection.json`

---

## 一、系统架构总览

### 1.1 双层存储架构

记忆系统由两套独立但互补的存储层构成：

| 层级 | 技术 | 路径 | 特点 |
|------|------|------|------|
| **结构化存储** | SQLite + FTS5 + Embedding | `config/memory/data/agent_memory.sqlite3` | 精确检索、语义搜索、衰减评分 |
| **便签存储** | Markdown 文件 | `config/memory/*.md` | 人类可读、段落级编辑、系统启动时注入 |

### 1.2 数据库 Schema

**memories 表**（核心）：

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | INTEGER PK | 自增主键 |
| `type` | TEXT | episodic / semantic / entity / reflection / permanent |
| `content` | TEXT | 记忆正文 |
| `source` | TEXT | 来源标识（如 `entity_123`、`reflect_global`、`merge`） |
| `importance` | REAL | 重要性 0–1 |
| `ts_ns` | INTEGER | 写入时间戳（纳秒） |
| `metadata_json` | TEXT | 元数据 JSON |
| `embedding_blob` | BLOB | 向量表示 |
| `tags_json` | TEXT | 标签 JSON 数组 |
| `access_count` | INTEGER | 被召回次数（隐式反馈） |
| `last_accessed_ns` | INTEGER | 最后被召回时间 |
| `migrated` | INTEGER | 迁移标记 |

**辅助表**：

| 表 | 用途 |
|----|------|
| `chunks` | MD 文件分块索引（path, start_line, end_line, text, embedding） |
| `files` | 文件变更追踪（path, hash, mtime_ns, size） |
| `embedding_cache` | Embedding 结果缓存 |
| `memories_fts` | FTS5 全文索引（自动触发器同步） |
| `chunks_fts` | 分块 FTS5 索引 |

**混合评分公式**：

```
final_score = semantic × 0.7 + decay × 0.3

semantic = vector_sim × 0.6 + fts_score × 0.25 + tag_match × 0.15
decay    = recency × 0.5 + frequency × 0.3 + importance × 0.2

recency: 30 天半衰期指数衰减
frequency: log(1 + access_count) / log(1 + max_access)
```

### 1.3 Markdown 便签文件

| 文件 | 行数 | 当前定位 |
|------|------|----------|
| `MEMORY.md` | 25 | 主便签，启动时注入为 `[个人笔记/便签记忆]` |
| `knowledge.md` | 280 | 事实知识（主人信息、群组、技术配置） |
| `tool_knowledge.md` | 177 | 工具使用经验 |
| `reflections.md` | 1134 | 反思记录 |
| `entities.md` | 1551 | 实体画像 |
| `heartbeat.md` | 259 | 心跳日志（自动滚动） |

### 1.4 数据流

```
=== 写入路径 ===

  memorize 工具 ──→ MemoryStore.add()        ──→ memories 表
  update_entity_profile ──→ MemoryStore       ──→ memories 表 (type=entity)
  write_notes / write_section ──→ 文件系统     ──→ config/memory/*.md
  memory_sync.sync_files ──→ chunks 表        ──→ MD 文件分块索引
  introspection ──→ MemoryStore.add()         ──→ memories 表 (type=reflection/entity)

=== 被动召回路径（每次 reply 自动触发） ===

  对话尾部 → extract_query → search_unified
                              ├─ memories: search_hybrid (vector + FTS + LIKE)
                              └─ chunks: vector + FTS + LIKE
                              → 合并排序 → 注入 LLM context

=== 主动召回路径（AI 调用 recall 工具） ===

  recall(query) → search_unified → JSON 返回给 AI

=== 便签注入路径（每次 reply 自动） ===

  MEMORY.md → build_notes_system_message() → system 消息 [个人笔记/便签记忆]
```

### 1.5 LLM 上下文消息顺序

```
1. system: 人设 + 便签（MEMORY.md 内容）
2. system: 工作记忆（工具规范 + 短期记忆 + 执行摘要）
3. head:   对话历史前段（超过 20 条时压缩为摘要）
4. system: 语义召回记忆（memories + chunks + 实体画像）
5. tail:   最近 10 条对话
```

### 1.6 记忆类型体系

| DB 类型 | 说明 | 对应 tag | 典型来源 |
|---------|------|----------|----------|
| `episodic` | 事件记忆 | `type:event` | AI 主动 memorize |
| `semantic` | 事实/知识 | `type:fact` | AI 主动 memorize |
| `entity` | 实体画像 | `type:entity` / `type:trait` | update_entity_profile / entity_analysis |
| `reflection` | 反思总结 | `type:reflection` | self_reflection / tool_usage_review |
| `permanent` | 永久记忆 | `type:permanent` | AI 主动 memorize（不会被清理） |

---

## 二、发现的问题

### 问题 1：MEMORY.md 缺乏引导作用

**现状**：MEMORY.md 仅 25 行，是一个杂乱的状态备忘录，内容如"mac语音转换：已完成"。

**影响**：
- AI 不知道记忆系统有哪些能力（DB 搜索、便签编辑、分块检索）
- AI 不知道 6 个 MD 文件各自的用途和写入标准
- AI 在反思时随意存放信息，导致分类混乱
- MEMORY.md 作为唯一自动注入的便签，应承担"记忆系统使用指南"的角色

**代码关联**：
- `notes.py:412-417` — `build_notes_system_message()` 将 MEMORY.md 内容作为 system 消息注入
- 空便签有引导提示（`_NOTES_EMPTY_HINT`），但非空时完全没有系统级使用指南
- AI 必须主动调用 `list_memory_files` 才能发现其他文件

### 问题 2：无工具错误持久化追踪

**现状**：
- `PFC._tool_recall` 仅记录工具调用命中计数，不记录错误
- `tool_knowledge.md` 有一些手写经验，但全靠 AI 自觉总结，无结构化数据
- `tool_usage_review` 内省单元存在，但产出写入 reflection 类通用记忆，无专门数据
- 实际案例：`recognize_image` 因 `media_file` 参数名错误连续失败 14 次，AI 无法从历史错误中学习

**影响**：
- 相同的工具调用错误会反复出现
- AI 反思时无法精确回顾"哪些工具出了什么错"
- `tool_usage_review` 单元缺乏数据支撑，只能泛泛而谈

### 问题 3：MD 文件分类混乱，无容量控制

**现状**：

| 文件 | 问题 |
|------|------|
| `entities.md`（1551 行） | 无分类头，部分画像与 DB entity 记忆重复 |
| `reflections.md`（1134 行） | 持续膨胀，早期反思未清理，新旧反思混杂 |
| `knowledge.md`（280 行） | 混杂了事实、规则、经验、人物关系，边界模糊 |
| `tool_knowledge.md`（177 行） | 结构较好，但无分类标准头 |
| `heartbeat.md`（259 行） | 有自动滚动机制，OK |
| `MEMORY.md`（25 行） | 应是索引/指南，实际是状态备忘 |

**影响**：
- AI 在反思整理时不知道该文件的容量上限
- `reflections.md` 和 `entities.md` 已超千行，chunks 检索效率下降
- 无头部元数据，AI 不知道"这个文件应该放什么、不应该放什么"

### 问题 4：DB 记忆类型与 MD 文件组织不对齐

**现状**：

DB 五种类型：`episodic` / `semantic` / `entity` / `reflection` / `permanent`

MD 六个文件：`MEMORY` / `knowledge` / `tool_knowledge` / `reflections` / `entities` / `heartbeat`

两套体系没有明确的映射关系：
- `knowledge.md` 里的内容应该是 DB 的 `semantic` 类型吗？
- `entities.md` 和 DB 的 `entity` 类型明显重复
- AI 不知道何时该用 `memorize` 写 DB，何时该用 `write_section` 写 MD

**影响**：
- 信息双重存储但不完全一致
- AI 在 memorize 时不确定该用什么 type/tag
- 召回时 DB 和 chunks 两路搜索可能返回重复内容

### 问题 5：便签系统缺少自描述能力

**现状**：
- `build_notes_system_message()` 仅注入 MEMORY.md 内容，不附带任何系统说明
- 空便签时有 `_NOTES_EMPTY_HINT` 引导，非空时完全没有使用提示
- 其他 5 个 MD 文件对 AI 完全"隐形"，除非它主动调用 `list_memory_files`
- 反思 prompt 中提到"可用 memorize/recall/list_conversations"，但未说明 MD 文件的角色

**影响**：
- AI 不知道有 `view_memory_outline`、`read_section`、`write_section` 等段落级工具
- AI 不知道应该在反思时检查和整理 MD 文件
- MEMORY.md 的指南作用完全浪费

---

## 三、优化方案

### 方案 A：MEMORY.md 重构为记忆索引与使用指南

将 MEMORY.md 改造为三区结构：

```markdown
# 记忆系统指南

<!-- 以下为记忆系统使用说明，可根据经验自行调整 -->

## 存储体系

你拥有两套记忆存储：

### 数据库记忆（通过 memorize / recall 工具操作）
- **episodic**（事件）：某人某时做了什么、发生了什么事
- **semantic**（知识）：事实、规则、技巧
- **entity**（实体）：用户/群组画像（由 update_entity_profile 管理）
- **reflection**（反思）：自我反思总结
- **permanent**（永久）：绝不遗忘的核心信息（用 type:permanent 标签）

### 便签文件（通过 read_section / write_section 等工具操作）
- `MEMORY.md`（本文件）：使用指南 + 当前状态 + 待办
- `knowledge.md`：长期知识（主人信息、群组规则、技术配置）
- `tool_knowledge.md`：工具使用经验和错误教训
- `reflections.md`：反思记录（保留近 2 周，定期归档清理）
- `entities.md`：实体画像详情（与 DB entity 记忆同步）
- `heartbeat.md`：心跳日志（系统自动管理）

## 使用原则

1. **短期/检索用** → DB（memorize），适合精确语义搜索
2. **长期/浏览用** → MD 文件（write_section），适合结构化阅读
3. **反思整理时**：先 view_memory_outline 查看结构，再按分类标准写入
4. **重复信息**：同一信息不要同时存 DB 和 MD，选一个主存储
5. **容量控制**：reflections.md 超过 500 行时归档旧内容

---

# 当前状态

（AI 维护的动态区域）
...
```

**实施方式**：直接重写 `config/memory/MEMORY.md`，不需要改代码。

### 方案 B：每个 MD 文件添加分类标准头

在每个 MD 文件顶部添加 HTML 注释区域，说明该文件的定位和规则。AI 整理记忆时读取头部即可知道规范，也可以自行调整：

```markdown
<!--
文件定位：工具使用经验与错误教训
写入标准：
  - 工具调用的成功经验和最佳实践
  - 工具错误的根因分析和解决方案
  - 参数使用注意事项
容量控制：保持在 300 行以内，定期合并相似条目
更新时机：tool_usage_review 反思时、遇到新的工具问题时
-->
# 梦璃的工具使用知识手册
...
```

**实施方式**：编辑每个 MD 文件，在顶部添加注释块。不需要改代码（`view_memory_outline` 和 `read_section` 已经能正确处理 HTML 注释）。

### 方案 C：新增工具错误追踪表

在现有 SQLite DB 中新增 `tool_errors` 表：

```sql
CREATE TABLE IF NOT EXISTS tool_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name   TEXT NOT NULL,           -- 工具名称
    error_type  TEXT NOT NULL DEFAULT '', -- 错误类型（参数错误/路径错误/超时/权限等）
    error_msg   TEXT NOT NULL,           -- 错误信息
    args_json   TEXT NOT NULL DEFAULT '{}', -- 调用参数（脱敏）
    context     TEXT NOT NULL DEFAULT '', -- 触发场景简述
    resolved    INTEGER NOT NULL DEFAULT 0, -- 是否已解决/已总结
    ts_ns       INTEGER NOT NULL         -- 时间戳
);
CREATE INDEX IF NOT EXISTS idx_te_tool ON tool_errors(tool_name);
CREATE INDEX IF NOT EXISTS idx_te_ts ON tool_errors(ts_ns);
```

**涉及的代码修改**：

| 文件 | 修改内容 |
|------|----------|
| `memory_store.py` `_init_schema()` | 新增 `tool_errors` 表 DDL |
| `memory_store.py` | 新增 `record_tool_error()` 和 `get_tool_errors()` 方法 |
| `think_loop.py` `execute_one_tool()` | 工具执行失败时调用 `record_tool_error()` |
| `memory/tools.py` | 新增 `recall_tool_errors(tool_name)` 工具供 AI 查询 |
| `introspection/` 配置型单元 `tool_usage_review` | prompt 中引导 AI 查询 tool_errors 表 |

**错误记录时机**：`think_loop.py` 的 `execute_one_tool()` 在 except 分支中自动写入。

**AI 查询方式**：新增一个 `recall_tool_errors` 工具，支持按工具名、时间范围、是否已解决等条件查询。

**与反思整合**：`tool_usage_review` 内省单元的 prompt 中增加"先查询 recall_tool_errors 了解近期错误，再总结经验写入 tool_knowledge.md"。

### 方案 D：优化反思时的记忆整理提示

**现状**：反思 prompt（`self_reflection` 单元）只说"记下重要的人和事"，没有引导 AI 按文件分类规则整理。

**优化**：在 `build_notes_system_message()` 注入 MEMORY.md 时，如果 MEMORY.md 包含"记忆系统指南"部分，AI 就能在反思时参考这些规则。结合方案 A，不需要额外改代码。

**额外改进**（可选）：在 `memory_consolidation` 内省单元的 prompt 中补充：
- "检查各 MD 文件是否超出容量限制"
- "合并 DB 中重复的 entity 记忆"
- "清理超过 2 周的 reflection 记忆"

---

## 四、实施模板

### 4.1 MEMORY.md 重构模板

```markdown
# 记忆系统指南

<!-- 以下为记忆系统使用说明，可根据经验自行调整 -->

## 存储体系

你拥有两套记忆存储：

### 数据库记忆（通过 memorize / recall 工具操作）
| 类型 | 用途 | 标签 | 示例 |
|------|------|------|------|
| episodic | 事件记忆 | type:event | "主人 3/14 说不用管 mac 的事" |
| semantic | 知识事实 | type:fact | "QQ 群 1104224649 是主人的社交群" |
| entity | 实体画像 | type:entity | 由 update_entity_profile 自动管理 |
| reflection | 反思总结 | type:reflection | 由内省系统自动写入 |
| permanent | 永久信息 | type:permanent | 绝不遗忘的核心规则或承诺 |

### 便签文件（通过 view_memory_outline / read_section / write_section 操作）
| 文件 | 定位 | 容量建议 |
|------|------|----------|
| MEMORY.md | 系统指南 + 当前状态 + 待办 | 不限 |
| knowledge.md | 长期知识：主人信息、群规、技术配置 | 500 行 |
| tool_knowledge.md | 工具经验：最佳实践、错误教训 | 300 行 |
| reflections.md | 反思记录：保留近 2 周 | 800 行 |
| entities.md | 实体详情：用户画像、群组画像 | 1000 行 |
| heartbeat.md | 心跳日志（系统自动管理） | 自动滚动 |

## 使用原则

1. **需要精确语义检索** → 用 memorize 存 DB
2. **需要结构化浏览** → 用 write_section 写 MD 文件
3. **同一信息不要双存** → 选择一个主存储
4. **反思整理时** → 先 view_memory_outline 看结构，按本指南分类写入
5. **超容量时** → 合并相似条目、归档旧内容、删除冗余

## 常见操作速查

- 查看某文件结构：`view_memory_outline(file_path="memory/knowledge.md")`
- 读取某章节：`read_section(file_path="memory/knowledge.md", heading="## 主人信息")`
- 写入某章节：`write_section(file_path="memory/knowledge.md", heading="## 新章节", content="...")`
- DB 语义搜索：`recall(query="关键词")`
- DB 按标签浏览：`memory_index(tag="type:permanent")`
- 工具错误查询：`recall_tool_errors(tool_name="recognize_image")`

---

# 当前状态

（以下为动态区域，自行维护）

## 待办事项
- （待更新）

## 近期重要事项
- （待更新）

## 待确认事项
- uid:939149454 的身份确认（待后续互动确认）
```

### 4.2 MD 文件分类标准头模板

**knowledge.md 头部**：

```markdown
<!--
文件定位：长期知识库
内容范围：
  - 主人信息（账号、喜好、习惯）
  - 群组信息（定位、规则、成员速查）
  - 技术配置（API、MCP、已知问题）
  - 通信规则（频道使用、@ 规范）
不应包含：
  - 实体画像详情（→ entities.md）
  - 工具使用经验（→ tool_knowledge.md）
  - 反思总结（→ reflections.md）
容量建议：500 行以内
-->
```

**tool_knowledge.md 头部**：

```markdown
<!--
文件定位：工具使用经验手册
内容范围：
  - 工具调用最佳实践
  - 参数使用注意事项
  - 错误处理经验（根因 + 解决方案）
  - MCP 服务使用经验
不应包含：
  - 通用知识（→ knowledge.md）
  - 特定人物信息（→ entities.md）
容量建议：300 行以内
更新时机：tool_usage_review 反思时 / 遇到新的工具问题时
-->
```

**reflections.md 头部**：

```markdown
<!--
文件定位：反思记录
内容范围：
  - 自我反思（记忆、自检、发现、计划）
  - 阶段性总结
  - 主人反馈与教训
保留策略：保留近 2 周的反思，更早的归档清理
容量建议：800 行以内
整理时机：memory_consolidation 反思时
-->
```

**entities.md 头部**：

```markdown
<!--
文件定位：实体画像详情
内容范围：
  - 群组画像（定位、成员、事件）
  - 用户画像（身份、印象、喜好、关系）
  - 跨平台实体关联
同步说明：
  - 与 DB entity 记忆保持一致
  - update_entity_profile 更新 DB，重要变更同步到此文件
容量建议：1000 行以内（超过时合并相似画像）
-->
```

### 4.3 tool_errors 表设计

```sql
CREATE TABLE IF NOT EXISTS tool_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name   TEXT NOT NULL,
    error_type  TEXT NOT NULL DEFAULT '',
    error_msg   TEXT NOT NULL,
    args_json   TEXT NOT NULL DEFAULT '{}',
    context     TEXT NOT NULL DEFAULT '',
    resolved    INTEGER NOT NULL DEFAULT 0,
    ts_ns       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_te_tool ON tool_errors(tool_name);
CREATE INDEX IF NOT EXISTS idx_te_ts ON tool_errors(ts_ns);
```

**记录时机**（`think_loop.py` → `execute_one_tool()`）：

```python
# 工具执行失败时自动记录
except Exception as exc:
    if mind.memory_store:
        await mind.memory_store.record_tool_error(
            tool_name=tc.name,
            error_type=type(exc).__name__,
            error_msg=str(exc),
            args_json=tc.arguments[:500],
        )
```

**查询工具**（`memory/tools.py`）：

```python
@deferred_tool(group="memory", tags=["core", "reflect"])
async def recall_tool_errors(tool_name: str = "", limit: int = 20) -> str:
    """查询工具调用错误历史，用于反思和总结经验。

    Args:
        tool_name: 工具名称，空则返回所有工具的错误统计
        limit: 返回条数上限
    """
```

**与 tool_usage_review 整合**：

```
// introspection 单元 prompt 追加：
"在反思工具使用前，先调用 recall_tool_errors 查看近期工具错误记录，
 分析错误模式并总结经验写入 tool_knowledge.md。
 已总结的错误标记为 resolved。"
```

### 方案 E：便签注入增加文件索引摘要

**现状**：`build_notes_system_message()` 只注入 MEMORY.md 内容，其他 5 个 MD 文件对 AI 完全"隐形"。AI 每次对话都"忘了"自己积累的工具经验、知识等，必须主动调工具才能发现。

**优化**：在 `build_notes_system_message()` 注入 MEMORY.md 后，追加一段**文件索引摘要**（文件名 + 行数 + 用途提示），让 AI 知道有哪些记忆文件可以查阅。

**涉及代码**：`agent/core/mind/memory/notes.py` → `build_notes_system_message()`

```python
# 在注入 MEMORY.md 内容后追加文件索引
files = list_all_memory_files()
if files:
    index_lines = ["[可用便签文件]"]
    for f in files:
        if f["name"] != "MEMORY.md":
            index_lines.append(f"  - {f['name']} ({f['lines']} 行): 用 view_memory_outline 查看结构")
    content += "\n\n" + "\n".join(index_lines)
```

### 方案 F：实体画像去重，消除三重存储

**现状**：实体画像同时存在于三个位置：

| 位置 | 写入方式 | 读取方式 |
|------|----------|----------|
| `EverythingData` entity personality | `update_entity_profile` 自动写入 | `entity_analysis` 内省自动读取 |
| `MemoryStore` type=entity 记忆 | `update_entity_profile` 自动同步 | `MemoryRetriever` 被动召回注入 |
| `entities.md` MD 文件 | AI 在反思时手动维护 | AI 主动 `read_section` 查看 |

三者内容随时间推移必然不一致。

**优化**：
1. 废弃 `entities.md` 作为画像主存储，改为"实体索引"角色（只列出已知实体的概要，不存完整画像）
2. 完整画像统一由 DB entity 记忆 + EverythingData 管理
3. 或者在 `update_entity_profile` 中增加自动同步到 MD 的逻辑

**涉及代码**：`agent/core/mind/memory/tools.py` → `update_entity_profile()`

### 方案 G：标签体系规范化

**现状**：`memorize` 工具的 tags 参数描述仅为"标签，逗号分隔（如 user:123,topic:编程,type:fact）"。AI 不知道：
- 推荐的 tag 前缀有哪些
- 什么场景该打什么标签
- 标签对召回评分的影响（tag_match 占语义评分 15%）

**优化**：
1. 在 MEMORY.md 指南区域增加标签规范表
2. 在 `memorize` 工具的 docstring 中补充推荐标签列表

**推荐标签体系**：

| 前缀 | 用途 | 示例 |
|------|------|------|
| `type:` | 记忆类型 | `type:fact`, `type:event`, `type:permanent` |
| `user:` | 关联用户 | `user:1292917512` |
| `group:` | 关联群组 | `group:1104224649` |
| `topic:` | 主题分类 | `topic:编程`, `topic:游戏` |
| `channel:` | 来源频道 | `channel:qq`, `channel:telegram` |
| `merged` | 合并标记 | 表示该记忆由多条合并而来 |

### 方案 H：被动召回查询提取优化

**现状**：`MemoryRetriever._extract_query()` 仅取最近 5 条对话文本直接拼接（限 500 字符），用于语义搜索。

**问题**：
- 日常闲聊文本做语义搜索效果差（"哈哈好的"之类的对话无法匹配有意义的记忆）
- 没有关键词提取或意图识别
- 多轮对话中可能丢失核心话题

**优化方向**：
1. 过滤无意义的短消息（< 5 字符的纯表情/语气词）
2. 优先使用 user 消息而非 assistant 消息作为查询源
3. 可选：在查询前用 LLM 做一次轻量级的关键词提取（但会增加延迟）

**涉及代码**：`agent/core/mind/memory/memory_retriever.py` → `_extract_query()`

### 方案 I：反思产出自动同步 MD 文件

**现状**：反思系统（`self_reflection`、`tool_usage_review` 等）的产出只写入 DB（作为 reflection 类记忆），不会自动更新 MD 文件。AI 必须在反思过程中主动调用 `write_section` 才能更新 `reflections.md` 或 `tool_knowledge.md`，实际效果是 DB 中堆积大量 reflection 记忆，MD 文件更新滞后。

**优化方向**：
1. 在 `_store_result()` 存储反思结果后，根据 `unit_name` 自动追加到对应 MD 文件
2. `self_reflection` → `reflections.md`
3. `tool_usage_review` → `tool_knowledge.md`
4. 追加时自动检查容量，超过阈值触发归档

**涉及代码**：`agent/core/mind/introspection/orchestrator.py` → `_store_result()`

---

## 五、实施优先级

| 优先级 | 方案 | 工作量 | 影响面 | 类型 |
|--------|------|--------|--------|------|
| P0 | A — MEMORY.md 重构为使用指南 | 小 | 高：每次对话都会注入 | MD |
| P0 | B — MD 文件添加分类标准头 | 小 | 中：反思整理时参考 | MD |
| P0 | G — 标签体系规范化 | 小 | 中：提升召回质量 | MD + docstring |
| P1 | C — tool_errors 表 | 中 | 高：解决错误重复问题 | 代码 |
| P1 | E — 便签注入增加文件索引 | 小 | 高：AI 知道自己的记忆文件 | 代码 |
| P1 | F — 实体画像去重 | 中 | 中：消除三重存储不一致 | 代码 + MD |
| P2 | D — 反思 prompt 优化 | 小 | 中：提升反思质量 | 配置 |
| P2 | H — 被动召回查询优化 | 小 | 中：提升召回精度 | 代码 |
| P2 | I — 反思产出自动同步 MD | 中 | 中：保持 DB/MD 一致 | 代码 |

实施路线：
1. **第一阶段（零代码）**：A + B + G — 重构 MD 文件，立即生效
2. **第二阶段（核心代码）**：C + E — tool_errors 表 + 便签索引注入
3. **第三阶段（优化迭代）**：D + F + H + I — 反思优化、实体去重、召回优化

---

## 六、当前 MD 文件内容问题速查（基于 2026-03-15 最新数据）

### MEMORY.md（26 行）
- 仍然是状态备忘录，但已新增"今日新教导"区域
- 主人已授权 AI "创建新的笔记记忆，分类整理让记忆更有体系"
- 这恰好验证了方案 A 的必要性——AI 已被授权自我组织记忆，但缺少指南

### knowledge.md（268 行）— **灾难级混乱**

文件前 81 行结构良好（主人信息、通信规则、群组等），但第 82 行开始完全失控：

**结构崩塌统计**：
- `## 语义知识` 标题重复出现 **10 次**（第 82/86/103/154/158/171/193/197/201/238 行）
- `## 工具使用最佳实践` 标题重复 **2 次**（第 216/217 行）
- Goal JSON 原始数据被**逐字 dump** 7+ 次（含同一个 goal 的多次状态变更）
- 实体画像（用户画像模板）被混入（第 106-136 行，应在 entities.md）
- 系统诉求、反思总结、主动联络记录全部混入
- 操作日志（"梦璃检查完毕啦~"）被当知识存储

**根因**：AI 在 memorize 时没有分类引导，所有信息都往 knowledge.md 堆。反思和内省单元的产出也被无差别写入。

**应保留**：第 1-81 行的结构化知识
**应清理**：第 82-268 行全部（迁移有效内容后删除）

### tool_knowledge.md（221 行）
- 结构仍然较好，分章节组织
- 新增了"系统反思经验"和"web_fetch 报错处理"等实用内容
- 但第 178-179 行出现重复标题（`### ✅ 系统反思（2026-03-15）` 和 `### ✅ 系统反思经验（2026-03-15）`）
- 部分内容与 knowledge.md 的"已知问题"和"图片识别经验"重叠
- 缺少分类标准头

### reflections.md（1193 行）
- 最早反思记录来自 2026-03-02，已近 2 周
- 包含大量冗余的群聊成员身份辨析表（至少重复 3 次）
- 新旧反思混杂，未做归档清理
- 建议只保留近 1 周，更早的合并为摘要

### entities.md（1551 行）
- 包含群组和用户画像
- 与 DB 中 `source=entity_xxx` 的记忆存在三重重复
- 同时 knowledge.md 中也混入了用户画像（第 106-136 行）
- 部分用户画像过于详细（包含每次互动记录），应精简

### heartbeat.md（259 行）
- 有自动滚动机制（`consolidate_heartbeat`），无需干预

---

## 七、AI 视角的完整工作流追踪

以下从 AI 实际接收数据的角度，逐步追踪一次对话回复中记忆的获取、组装、注入全过程，标注每个环节的数据损耗。

### 7.1 一次对话回复的完整记忆流

```
消息到达 → accept_feel() → PFC 任务队列
     ↓
自主循环 → _gather_situation()
  └─ recent_memories = store.list_recent(5)  ← 每条截断100字符
     ↓
元决策 → _think_and_decide()
  ├─ memory_ctx = retriever.recall(pending_previews, top_k=5)
  │   └─ 使用消息预览作查询，结果注入为 user 角色
  └─ situation.to_summary() → "[近期记忆] N 条: - mem1..."
     ↓
决策=REPLY → reply_loop() → get_recollection()
  ├─ conversation_list = get_conversation()     ← 第一次获取对话历史
  ├─ tail = conversation_list[-10:]
  ├─ query = _extract_query(tail)               ← ⚠ 只取最近5条，500字上限
  ├─ query_vec = embedder.embed_one(query)
  ├─ results = search_unified(query, query_vec)  ← ⚠ min_score=0.1 过低
  ├─ entity_profiles = _load_entity_profiles()   ← 仅加载 tail 中出现的 UID
  └─ memory_msgs = [人物画像] + [记忆召回] + [知识检索]  ← ⚠ 元数据丢失
     ↓
pfc.build_llm_context()
  ├─ conversation_list = get_conversation()      ← ⚠ 第二次获取（重复+可能不一致）
  ├─ system: 人设 + MEMORY.md                    ← ⚠ 其他MD文件不可见
  ├─ system: 工具规范 + 短期记忆
  ├─ head: 前段对话                              ← ⚠ >20条时极度压缩
  ├─ user: memory_msgs（system→user 角色转换）    ← ⚠ AI可能混淆为用户发言
  └─ tail: 最近10条对话
```

### 7.2 逐环节数据损耗分析

#### 损耗点 1：查询提取过于粗糙（`_extract_query`）

```python
# memory_retriever.py:171-191
for msg in reversed(conversation):
    if role == "assistant":
        cleaned = cleaned[:100]  # assistant 只取前100字
    texts.append(cleaned)
    if len(texts) >= 5:
        break
return " ".join(reversed(texts))[:500]
```

**问题**：
- 不过滤无意义短消息（"好的"、"哈哈"、纯表情），拉低查询质量
- assistant 截断到 100 字可能丢失关键信息（如工具调用结果中的重要内容）
- 5 条上限过少，多轮对话中核心话题可能不在最近 5 条
- user 和 assistant 混合拼接，语义信号被稀释

#### 损耗点 2：对话历史重复获取且可能不一致

```python
# mind.py:get_recollection()
conversation_list = await self.get_conversation(anything)  # 第一次获取
tail = conversation_list[-10:]
memory_msgs = await self.retriever.recall(tail, ...)

# prefrontal_cortex.py:build_llm_context()
conversation_list = await self._conversation_data.get_conversation_record_by_everything(anything)  # 第二次获取
```

两次独立从 DB 获取对话历史。如果期间有新消息写入 DB，两次拿到的 tail 不一致——召回用的查询和最终展示的对话不匹配。

#### 损耗点 3：历史对话压缩极度有损（`_compress_head`）

```python
# prefrontal_cortex.py:506-516
for msg in head[:5]:           # 只看前5条
    snippet = content.strip()[:60]  # 每条只取60字符
summary = f"[历史摘要] 前 {len(head)} 条对话已省略，涉及：{topics}"
```

**问题**：
- 100 条对话历史（head=90 条）只用前 5 条的前 60 字生成摘要
- 中间 85 条对话完全丢失，AI 对之前的对话内容毫无记忆
- 没有用 LLM 做真正的摘要，只是简单截断拼接
- 这意味着长对话中 AI 会"忘记"很多重要上下文

#### 损耗点 4：召回结果元数据被丢弃（`_format_unified_results`）

```python
# memory_retriever.py:193-217
if r.source == "file":
    file_lines.append(f"{prefix}{r.snippet}")
else:
    tag_prefix = f"[{','.join(r.tags)}] "
    mem_lines.append(f"{tag_prefix}{r.snippet}")
```

格式化后 AI 只能看到标签和内容文本，以下信息全部丢失：
- **相关度评分**（score）：AI 不知道哪条最相关
- **记忆 ID**（id）：AI 无法引用、更新或删除特定记忆
- **记忆类型**（type）：AI 不知道这是事件记忆还是永久记忆
- **重要性**（importance）：AI 不知道哪条更重要
- **来源**（source）：AI 不知道这条记忆是反思产出还是手动存入

#### 损耗点 5：记忆角色转换的权衡

```python
# prefrontal_cortex.py:build_llm_context()
context_msgs = [
    {**m, "role": "user"} if m.get("role") == "system" else m
    for m in memory_msgs
]
```

记忆召回结果（`[人物画像]`、`[记忆召回]`、`[知识检索]`）原本是 `system` 角色，被转为 `user` 角色注入。**这是有意设计**——防止 AI 把记忆当作自己说过的话而制造假工具调用（工具幻觉）。但代价是 AI 可能尝试"回复"这些记忆内容。可以通过更醒目的系统标识前缀来缓解。

#### 损耗点 6：截断长度在各环节不一致

| 环节 | 截断长度 | 代码位置 |
|------|----------|----------|
| 元决策态势 | 100 字符 | `mind.py:_gather_situation()` |
| recall 工具返回 | 300 字符 | `tools.py:recall()` |
| memory_index 工具 | 80 字符 | `tools.py:memory_index()` |
| 被动召回注入 | 700 字符 | `memory_store.py:search_unified()` |
| 实体画像注入 | 完整内容 | `memory_retriever.py:_load_entity_profiles()` |

同一条记忆在不同场景下被截断到不同长度，AI 看到的是不一致的视图。

#### 损耗点 7：实体画像加载仅基于 tail 中的 UID

```python
# mind.py:get_recollection()
tail = conversation_list[-10:]
related_scopes = self._extract_related_scopes(tail, entity_scope)
```

只从最近 10 条消息中提取 `[uid:xxx]` 标签对应的用户画像。如果某个重要用户在第 15 条消息中被提到但后续没说话，其画像不会被加载，AI 可能不记得这个人。

#### 损耗点 8：min_score = 0.1 过低，噪声记忆可能通过

```
final = semantic × 0.7 + decay × 0.3
```

一条完全无语义匹配（semantic=0）但最近且重要的记忆：
- `decay = 1.0 × 0.5 + 0.0 × 0.3 + 1.0 × 0.2 = 0.7`
- `final = 0.0 × 0.7 + 0.7 × 0.3 = 0.21`

得分 0.21 > min_score 0.1，通过阈值。意味着**完全不相关但最近存入的高重要性记忆**会挤占有效结果。

### 7.3 AI 最终看到的上下文实际样例

```
[1] system: "你是梦璃...（人设）\n\n[个人笔记/便签记忆]\n梦璃当前状态...\n主人教导..."
[2] system: "[工具使用规范]\n1. 必须通过 function calling...\n# 工具分组目录\n- output (2)...\n- memory (4)..."
[3] system: "[上轮执行摘要] 共 3 轮\n→ 第1轮: 调用工具 [recall]\n→ 第2轮: ..."
[4] system: "[历史摘要] 前 45 条对话已省略，涉及：你好主人、今天天气不错、..."  ← 极度压缩
[5] user: "[人物画像]\n[uid:1292917512]\n# 实体画像...\n---\n[group_id:1104224649]\n..."  ← 角色=user
[6] user: "[记忆召回]\n[type:permanent,诉求] 2026-03-13 梦璃系统诉求...\n---\n..."  ← 角色=user
[7-16] user/assistant: （最近 10 条对话）
[17] system: "[系统提示] 新一轮对话开始 | 请仔细分析上下文后决定操作 | 最多 30 轮"
```

注意：第 [5][6] 条是记忆内容但角色为 `user`。这是有意设计——如果用 `assistant` 角色注入，AI 会误以为是自己说过的话，进而基于这些文本制造假工具调用（工具幻觉）。转为 `user` 角色是为了规避此问题，但代价是 AI 可能尝试"回复"这些记忆内容。需要在防幻觉和语义清晰之间权衡。

---

## 八、补充优化方案

### 方案 J：召回结果保留关键元数据

**问题**：`_format_unified_results()` 格式化后丢失 score/id/type/importance。

**优化**：在格式化时保留核心元数据，让 AI 能判断相关度和引用记忆。

```python
# 改进后的格式
"[记忆召回]\n"
"💡 [entity] [user:123,type:profile] score=0.85: 主人的画像...\n"
"💡 [permanent] [type:permanent,诉求] score=0.72: 梦璃系统诉求...\n"
"💡 [semantic] [topic:编程] score=0.45: Python异步编程..."
```

**涉及代码**：`memory_retriever.py` → `_format_unified_results()`

### 方案 K：对话历史获取去重

**问题**：`get_recollection()` 和 `build_llm_context()` 各自独立获取对话历史，浪费 + 可能不一致。

**优化**：在 `get_recollection()` 中获取一次，直接传给 `build_llm_context()`，不让后者重新获取。

**涉及代码**：`mind.py` → `get_recollection()` 和 `prefrontal_cortex.py` → `build_llm_context()`

### 方案 L：历史对话压缩改进

**问题**：`_compress_head()` 仅取前 5 条×60 字，中间大量对话完全丢失。

**优化方向**：
1. 均匀采样而非只取头部（如头 3 + 中间 2 + 尾 2）
2. 保留系统上下文消息（如工具执行摘要）不被压缩
3. 可选：用 LLM 做一次轻量摘要（权衡延迟和质量）

**涉及代码**：`prefrontal_cortex.py` → `_compress_head()`

### 方案 M：记忆注入角色与标识优化

**背景**：记忆召回结果被从 `system` 转为 `user` 角色注入，这是有意设计——防止 AI 把记忆内容当作自己说过的话，进而制造假工具调用（工具幻觉）。但代价是 AI 可能尝试"回复"这些记忆内容。

**约束**：
- 不能用 `assistant` 角色（会触发工具幻觉）
- 不能用中途 `system` 角色（部分模型不支持对话中间插入 system 消息）
- 因此只能用 `user` 角色，这是当前的最优折中

**优化方向**（在 `user` 角色前提下改善语义清晰度）：
1. 在记忆内容前添加更醒目的系统标识前缀（如 `[系统注入·记忆召回]`），降低 AI 误回复的概率
2. 考虑将记忆内容与真实用户消息用明确的分隔符区分

**涉及代码**：`memory_retriever.py` → `_format_unified_results()` 中的前缀格式

---

## 九、全量问题清单

| # | 类别 | 问题 | 影响 | 对应方案 | 优先级 |
|---|------|------|------|----------|--------|
| 1 | 引导 | MEMORY.md 无系统使用指南 | AI 不知道怎么用记忆系统 | A | P0 |
| 2 | 追踪 | 工具错误无持久化追踪 | 相同错误反复出现 | C | P1 |
| 3 | 引导 | MD 文件无分类标准头 | AI 整理记忆无规范 | B | P0 |
| 4 | 引导 | DB 类型与 MD 文件不对齐 | AI 不知道存哪里 | A | P0 |
| 5 | 可见性 | 便签系统缺少自描述 | AI 不知道有哪些记忆文件 | E | P1 |
| 6 | 一致性 | 实体画像三重存储 | 数据不一致 | F | P1 |
| 7 | 引导 | 标签体系缺少规范 | 召回质量低 | G | P0 |
| 8 | 同步 | 反思产出不同步 MD | DB/MD 内容脱节 | I | P2 |
| 9 | 召回质量 | 查询提取粗糙，不过滤无意义文本 | 语义搜索效果差 | H | P2 |
| 10 | 引导 | 反思 prompt 无文件分类引导 | 整理结果混乱 | D | P2 |
| 11 | 召回质量 | 召回结果元数据丢失（score/id/type） | AI 无法判断相关度和引用记忆 | J | P1 |
| 12 | 性能/一致性 | 对话历史重复获取 | 浪费 + 两次结果可能不一致 | K | P1 |
| 13 | 上下文质量 | 历史对话压缩极度有损 | 长对话中 AI 遗忘大量上下文 | L | P2 |
| 14 | 上下文质量 | 记忆注入被转为 user 角色 | AI 可能混淆记忆和用户发言 | M | P2 |
| 15 | 召回质量 | min_score=0.1 过低 | 不相关记忆挤占有效结果 | H | P2 |
| 16 | 上下文质量 | 截断长度各环节不一致 | AI 看到同一记忆的不同片段 | J | P2 |
| 17 | 召回质量 | 实体画像仅加载 tail 中 UID | 非近期提到的用户画像不加载 | H | P2 |
| 18 | 数据质量 | knowledge.md 82行后完全失控（10个重复标题、Goal JSON dump、混入画像/反思/日志） | chunks 索引被污染，召回大量噪声 | A+B | P0 |
| 19 | 数据质量 | tool_knowledge.md 出现重复标题和重叠内容 | 信息冗余 | B | P1 |
| 20 | 数据质量 | 同一信息在 knowledge/entities/reflections 三文件重复 | token 浪费、召回噪声 | F | P1 |

---

## 十、实施路线（更新）

| 阶段 | 方案 | 工作量 | 类型 |
|------|------|--------|------|
| **第一阶段：零代码** | A + B + G | 小 | 仅改 MD 文件 |
| **第二阶段：核心改进** | C + E + J + K | 中 | 代码修改 |
| **第三阶段：深度优化** | D + F + H + I + L + M | 大 | 架构级调整 |
