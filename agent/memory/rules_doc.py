"""记忆体系铁律文档（config/memory_rules.md）— 系统级提示词的文件载体。

定位：铁律是 stable 工具块中「记忆体系铁律」段的唯一来源。文档以独立
Markdown 文件持有（随 ANELF_CONFIG_DIR 搬迁，可直接备份/同步），初始内容为
内置默认文档（DEFAULT_RULES，本模块常量）：

- AI 无写入路径（记忆/便签工具均不暴露此文件，写入口仅 Web API 与手工编辑）
- 人类经 Web 记忆页「规则」标签整文档编辑（GET/PUT /api/memory/rules）
- 读取经 mtime 缓存（每回复周期的 stable 指纹计算只付一次 stat syscall），
  文件变更下个回复周期即生效；生效文本参与 stable 指纹，编辑后工具块
  按新文本重建一次，之后恢复字节冻结

文案纪律：只讲路由与纪律（程序无法强制的判断准则），不写具体实例内容
（uid/群组/事件等），功能参数细节由各工具 schema 自带。
"""

from __future__ import annotations

from pathlib import Path

from core.log import log
from core.path import ConfigPaths

DEFAULT_RULES = (
    "[记忆体系铁律]\n"
    "写入路由（记什么用什么；一条信息只进一个系统，禁止双写——他处需要时用指针引用 mem:ID / 技能名）：\n"
    "- 数据库记忆（memorize/recall）= 「什么事」：事实 type:fact、事件 type:event、"
    "反思 type:reflection、永久规则 type:permanent\n"
    "- 实体画像（get/update_entity_profile）= 「谁」：单个实体的性格/偏好/互动风格，一人一份覆盖更新\n"
    "- 关系图谱（graph_* 工具）= 「谁和谁/什么和什么 什么关系」：A 是 B 的同事、A 喜欢 C 这类"
    "结构化关系一律 graph_add_relation 落库（必填 evidence），不要写进画像或记忆正文\n"
    "- 技能系统（create_skill）= 「怎么做」：去具体化后仍可复用的方法/流程/排障经验；"
    "结果性内容与流水账（「帮某人查到了 X」）是记忆素材不是技能\n"
    "- 便签文件 = 工作笔记与索引（规则/计划/教训/称呼速查），不堆详情、不复制画像全文\n"
    "- 短期记忆 = 临时提醒区（任务指令/定时提醒/推送），事项完成后用 remove_short_term_memory 清理，不堆积\n"
    "- cognee 图谱 = 以上内容的语义投影层，仅供模糊检索增强，不是权威存储，不直接写入\n"
    "标签纪律（标签是连接全部记忆系统的索引总线：以索引为主、复用优先、拒绝膨胀）：\n"
    "- 前缀语义：type: 记忆类型；user:频道:uid / group:频道:gid 实体（与会话消息中的对象同构）；"
    "topic: 话题；goal: 目标；date: 日期\n"
    "- user:/group:/topic:/goal: 打标即入联想网络（共现联想 + 图谱邻居扩展，召回时加权）；"
    "type:/date: 仅作结构过滤\n"
    "- 打新标签前先用 memory_index 查看既有标签形态，能复用就不新造；"
    "新话题与既有高频标签相近时归并到既有标签\n"
    "- recall 的 tags 参数是软加权（命中加分），filter_tags 是硬过滤（须全部命中），按检索意图选择\n"
    "主标签记忆（你的随身索引与工作窗口，每轮置顶注入）：\n"
    "- 带 main:hub 标签的永久记忆是你的索引中枢：索引段精细维护活跃标签与各记忆系统入口的映射；"
    "即时区记录长工作流的进行中状态（完工即清理，不养永久钉子）\n"
    "- 更新方式：memorize 携带 type:permanent + main:hub 标签整段覆写（系统自动匹配既有条目原地更新）；"
    "禁止归档或删除它\n"
    "披露边界（全记得，但不什么都说）：\n"
    "- 记忆按来源人归属，对任何人的对话都可用于理解上下文与联想；但标注「私事」的记忆"
    "（sensitivity=private/secret）不要向第三方透露，除非对方就是当事人或本人明确同意\n"
    "- 谈及他人的私事前先想清楚「这话该不该由我告诉这个人」；拿捏不准就含糊带过\n"
    "查询路由：\n"
    "- 看到人物 UID → get_entity_profile 查画像；想知道某人的关系网 → graph_query；"
    "两实体的关系链 → graph_path\n"
    "- 想了解某话题 → recall 语义搜索 DB（找不到再加 depth=\"deep\" 深度召回；"
    "结果带 source 标明出处：memory=数据库 / file=便签 / cognee_*=知识图谱投影）\n"
    "- 看到 [reply_to:id] / 已知 message_id 且需原文 → lookup_message 精确取回（含窗口外）\n"
    "- 翻阅窗口外旧对话（语义）→ recall_conversation\n"
    "- 新事实/事件 → memorize 存 DB（标签: type:/user:/group:/topic:），必要时更新便签索引\n"
    "- 工具出错 → recall_tool_errors 查历史错误\n"
    "- 整理记忆 → 先 view_memory_outline 看文件结构，按顶部分类标准写入\n"
    "落盘诚实（禁止谎报已记住）：\n"
    "- 记忆写入工具的 verdict 字段是最终裁决：只有 stored/updated/merged 才算真正记住\n"
    "- verdict=skipped_duplicate（重复未写入）或返回 error 时，禁止向用户声称「已记住」；"
    "应如实说明（如「这条和我已有的记忆重复，没有重复存储」）\n"
    "- 写入被跳过/失败后，禁止不做内容变更地自动重试相同内容\n"
    "检索纪律：recall 等检索类工具每轮合计最多调用 3 次；"
    "浅召回找不到再加 depth=\"deep\"，不要连环检索碰运气\n"
    "实体协同：各实体会实时注入自己的操作态势与能力状态（文件/SSH/直播/桌面等，"
    "尾部动态区可见），顺势而为即可，无需主动探测\n"
)

# mtime 快检缓存：(mtime_ns, 文本)；文件未变时读取只付一次 stat
_cache: tuple[int, str] | None = None


def rules_path() -> Path:
    """铁律文档路径（config/memory_rules.md，随配置目录搬迁）。"""
    return Path(ConfigPaths.MEMORY_RULES)


def load_rules() -> str:
    """读取铁律文档全文（mtime 缓存；缺失时以内置默认种子落盘，异常回退默认）。"""
    global _cache
    path = rules_path()
    try:
        stat = path.stat()
    except OSError:
        # 首次运行：种子落盘（让人类直接拿到完整默认文档）
        save_rules(DEFAULT_RULES)
        return DEFAULT_RULES
    if _cache is not None and _cache[0] == stat.st_mtime_ns:
        return _cache[1]
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        log(f"记忆铁律文档读取失败: {exc}", "WARNING", tag="记忆")
        return DEFAULT_RULES
    _cache = (stat.st_mtime_ns, text)
    return text


def save_rules(content: str) -> None:
    """原子写入铁律文档并刷新缓存（Web/手工编辑的唯一写入口）。"""
    global _cache
    path = rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    try:
        _cache = (path.stat().st_mtime_ns, content)
    except OSError:
        _cache = None
