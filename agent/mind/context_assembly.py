"""ContextAssembly — LLM 上下文组装：系统提示构建、Prompt 分层缓存、执行上下文。

从 PrefrontalCortex 拆分而来。函数/类以组合方式持有 WorkMemory 与 ToolAssembly
（由 PFC 门面接线），不依赖 Mind。

职责：
- 工具系统提示构建（工具目录 + 使用规则 + 媒体处理规则 + 频道能力）
- stable 层构建与指纹（Prompt 分层缓存的稳定前缀）
- 完整 LLM 上下文组装（stable/context/volatile/对话历史分层）
- 每轮执行上下文（轮次/工具态势/频道/会话通知）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from core.entity import EntityRegistry
from core.log import log

if TYPE_CHECKING:
    from agent.channel.manager import ChannelManager
    from agent.messages import Everything
    from agent.mind.tool_assembly import ToolAssembly
    from agent.mind.work_memory import WorkMemory
    from agent.storage.data_center import ConversationData


def _get_mind_config():
    from agent.config import get_mind_config
    return get_mind_config()


def _delegation_enabled() -> bool:
    """子代理委托是否启用（后台任务规范提示的注入条件）。"""
    from core.config import get_config_bool
    return get_config_bool("delegation_enabled", True)


def _tail_injection_enabled() -> bool:
    """尾部动态注入布局开关（动态内容置于对话历史之后，保持历史前缀缓存稳定）。"""
    from core.config import get_config_bool
    return get_config_bool("context_tail_injection_enabled", True)


def _cap_names(names: List[str], limit: int = 8) -> str:
    """工具名列表截断展示：超限显示前 limit 个 + 总数（AI 只需感知规模，无需全量名单）。"""
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])} 等 {len(names)} 个"


def _env_info_block() -> str:
    """运行环境信息块（工作区操作环境 + 宿主环境 + 平台 + 命令方言），注入 stable 层。"""
    import platform as _platform

    from core.path import workspace_root
    system = _platform.system().lower()
    block = (
        "[运行环境]\n"
        f"工作区根目录: {workspace_root()}（你的操作环境）\n"
        f"平台: {system} ({_platform.machine()})\n"
        "文件工具与 Shell 的相对路径均基于工作区根目录，操作在工作区内进行，"
        "访问区外用绝对路径；Shell 当前工作目录等实时信息用 get_workspace_info。"
    )
    # 宿主环境事实：环境实体为单一数据源（与 get_python_status 工具共用
    # 检测逻辑），进程级缓存保证字节稳定（人设层指纹依赖）
    try:
        from entities.system.python_service import get_runtime_env_summary
        block += "\n" + get_runtime_env_summary()
    except Exception:
        pass
    if system in ("darwin", "freebsd", "openbsd", "netbsd"):
        block += (
            "\n命令方言: BSD 用户态（非 GNU）——find 无 -printf、sed -i 需备份后缀、"
            "stat 用 -f 而非 -c、date 用 -v 而非 -d、grep 无 -P"
        )
    return block


def _safe_entity_scope(anything: object) -> str:
    """兼容测试替身的 entity_scope 获取：缺失时按 adapter/uid/group_id 拼接。"""
    if anything is None:
        return ""
    scope = getattr(anything, "entity_scope", None)
    if scope:
        return str(scope)
    from agent.messages import build_entity_scope
    adapter = str(getattr(anything, "adapter_key", "") or "")
    gid = getattr(anything, "group_id", 0)
    if gid not in (0, "0", "", None):
        return build_entity_scope("group", adapter, str(gid))
    uid = getattr(anything, "uid", "")
    if uid in ("", 0, "0", None):
        return ""
    return build_entity_scope("user", adapter, str(uid))


def _format_scope_label(scope: str, adapter_key: str = "") -> str:
    """格式化 scope 的通知展示标签：频道 + 私聊/群聊 + 基 id + 子会话。"""
    from agent.messages import parse_entity_scope

    scope_type, scope_adapter, base_id, session_id = parse_entity_scope(scope)
    if not scope_type:
        return scope
    adapter_key = adapter_key or scope_adapter
    kind = "群聊" if scope_type == "group" else "私聊"
    parts = [adapter_key, kind, base_id] if adapter_key else [kind, base_id]
    label = " ".join(parts)
    if session_id:
        label += f"#{session_id}"
    return label


# ==================================================================
# 提示词模板常量
# ==================================================================

# 工具使用指引（仅保留无法程序强制的引导性内容；
# "失败勿重复"由工具守卫强制，
# 并行调用用法由 stable 层 _PARALLEL_CALL_HINT 统一提供，此处不重复）
_TOOL_USAGE_RULES = (
    "[工具使用指引]\n"
    "1. 如果返回“工具不存在/未知工具”，必须先调用 list_entity_methods 获取精确方法名，禁止继续猜测相似名称。\n"
    "2. 同一任务连续两次出现未知工具后，必须停止继续猜测并改用已确认可用工具，或直接结束并说明限制。"
)

_PLAN_USAGE_RULES = (
    "[计划模式指引]\n"
    "1. **默认走计划模式**：除最简单的单步问答/闲聊外，所有任务（信息搜集、目录分析、文件操作、代码修改、"
    "多步推理等任何需要 2 步以上的工作）都应**先调用 present_plan 再执行**——用户会在浮窗中实时看到你的计划与进度。\n"
    "2. \"公告\" = **调用 present_plan 工具**，不是用文字描述，禁止输出计划文本或步骤清单。\n"
    "3. present_plan(goal, steps, files, risks) 调用后立即返回 plan_id，无需等待用户批准，直接开始执行。\n"
    "4. 步骤进度由系统**自动追踪**（每步完成自动打勾），你无需手动维护进度。\n"
    "5. 如某步完成质量较好，**可选**调用 update_goal(goal_id=plan_id, step_index=i, step_status='completed', note='...') 精确标记。\n"
    "6. 任务完成后正常 end_reply 即可，系统会自动把计划收敛到终态（未做的步骤标记为 skipped）。\n"
    "7. 用户可在浮窗中取消计划，你会收到中断信号，需立即停止后续步骤。\n"
    "8. 仅以下情况不用 plan：单步问答、纯闲聊、一次工具调用就能完成的简单查询。"
)

_MEMORY_USAGE_HINT = (
    "[记忆使用提示]\n"
    "记忆系统职责边界（记什么用什么，禁止混写）：\n"
    "- 数据库记忆（memorize/recall）= 「什么事」：事实 type:fact、事件 type:event、"
    "反思 type:reflection、永久规则 type:permanent\n"
    "- 实体画像（get/update_entity_profile）= 「谁」：单个实体的性格/偏好/互动风格，一人一份覆盖更新\n"
    "- 关系图谱（graph_* 工具）= 「谁和谁/什么和什么 什么关系」：A 是 B 的同事、A 喜欢 C 这类"
    "结构化关系一律 graph_add_relation 落库（必填 evidence），不要写进画像或记忆正文\n"
    "- 便签文件 = 工作笔记与索引（规则/计划/教训/称呼速查），不堆详情、不复制画像全文\n"
    "- 短期记忆 = 临时提醒区（任务指令/定时提醒/推送），事项完成后用 remove_short_term_memory 清理，不堆积\n"
    "- 技能系统 = 工具使用经验/工作流技巧（create_skill，不要写成记忆，避免双写漂移）\n"
    "- cognee 图谱 = 以上内容的语义投影层，仅供模糊检索增强，不是权威存储，不直接写入\n"
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
    "浅召回找不到再加 depth=\"deep\"，不要连环检索碰运气"
)

_FINAL_ROUND_WARNING = (
    "⚠️ [最终轮次] 这是最后一轮机会，系统将在本轮后强制结束。"
    "请立即完成必要操作并调用 end_reply，不要再开新工具调用链。"
)

_URGENT_ROUND_WARNING = (
    "⚠️ [轮次告急] 仅剩 2 轮，请尽快收束操作并调用 end_reply。"
    "避免在此阶段开启复杂工具链。"
)

_NO_PENDING_HINT = "[当前无外部消息] 自主思考阶段：可执行工具操作，或调用 end_reply 结束"

_PARALLEL_CALL_HINT = (
    "# 并行工具调用\n"
    "同一轮可以发起多个工具调用（原生并行），参数已确定的独立操作应一次性全部发起，减少对话轮次。\n"
    "**先完成全部必要工具，再回复用户——回复一律调用 send_message**"
    "（工具调用，不结束本轮，中途进度也可随时发送）。\n"
    "**end_reply 会彻底结束本轮对话，不存在「下一轮再继续」——文字中声明要做的操作，"
    "必须在调用 end_reply 之前实际发起工具调用，只说不做等于放弃。**"
)

_BACKGROUND_TASK_HINT = (
    "# 后台任务\n"
    "delegate_task(background=true) 启动的后台任务，完成时系统会自动通知你（触发新一轮对话），无需守候。\n"
    "- 想查进度 → 调用 check_background_tasks\n"
    "- 想等结果 → 告知用户后调用 end_reply 结束本轮，完成时你会被自动唤醒"
)

_PENDING_HINT = "→ 处理消息或执行操作，完成后调用 end_reply。空消息表示自主思考阶段（非对方发送），不要重复发送消息"

# 会话通知：其他会话的未读消息以"弹窗"形式提示（固定模板，动态内容在末尾 exec_context）
_SESSION_NOTIFY_HINT = (
    "→ 回复默认发往当前会话，无需选择投递目标；"
    "如需处理其他会话的新消息，调用 switch_session(scope) 切换，"
    "可先调用 list_sessions 查看全部会话"
)


from agent.mind.context_pipeline import (
    VOL_HISTORY,
    VOL_MESSAGE,
    VOL_PERIODIC,
    VOL_SESSION,
    VOL_STABLE,
    VOL_TAIL_HEAD,
    ContextInput,
    ContextPipeline,
    context_block,
)

# legacy 布局的变动率覆盖表（tail_injection 关闭时）：动态块移到历史之前
_LEGACY_VOLATILITY: Dict[str, int] = {
    "context": 10,
    "status": 24, "volatile": 25, "provider": 26, "overflow": 27,
    "security": 28, "profile": 29, "relation": 30, "goals": 31, "memory": 32,
    "summary": 33, "conversation": 34,
}


class ContextAssembly:
    """LLM 上下文组装（PFC 上下文面组件）。"""

    def __init__(
            self,
            work_memory: "WorkMemory",
            tool_assembly: "ToolAssembly",
            channel_manager: Optional["ChannelManager"] = None,
            conversation_data: Optional["ConversationData"] = None,
    ) -> None:
        self._work_memory = work_memory
        self._tool_assembly = tool_assembly
        self._channel_manager = channel_manager
        self._conversation_data = conversation_data
        # stable_fingerprint 版本门控缓存：(tools_version, activation_version, models_summary, direct_vision) → hash
        self._fp_cache: Optional[tuple[int, int, str, bool, str]] = None
        # 上下文构建管线：默认布局（动态在历史之后）+ legacy 回退布局
        self._pipeline = ContextPipeline(self)
        self._pipeline_legacy = ContextPipeline(self, volatility_overrides=_LEGACY_VOLATILITY)

    # ==================================================================
    # 系统提示构建
    # ==================================================================

    def build_tool_system_prompt(
            self,
            models_summary: str = "",
            adapter_key: str = "",
            target_id: str = "",
            direct_vision: bool = False,
    ) -> list[dict]:
        """构建工具使用规则、通道感知、媒体处理规则的系统提示。"""
        catalog = EntityRegistry.get_entity_catalog()
        if not catalog:
            return []

        mc = _get_mind_config()
        rules = mc.tool_system_rules if hasattr(mc, "tool_system_rules") else []
        lines = list(rules) + ["# 工具分组目录"]

        # 可沉睡分组：目录文案保持静态（不随激活状态变化）——激活状态是
        # 按 scope 隔离的动态量，嵌进 stable 块会导致文本随 scope/激活波动，
        # 击穿缓存前缀；当前激活状态由 exec_context 的 [已激活工具分组] 呈现
        sleepable_groups = EntityRegistry.get_sleepable_groups()

        for entry in catalog:
            group = entry["group"]
            desc = entry.get("description", "")
            desc_part = f" — {desc}" if desc else ""
            sleep_info = sleepable_groups.get(group)
            if sleep_info:
                lines.append(
                    f"- {group} ({entry['tool_count']}){desc_part} "
                    f"[可沉睡] {sleep_info['brief']}"
                    f"（完整工具默认沉睡，需要时调用 activate_tool_group(group=\"{group}\") 激活）"
                )
            else:
                lines.append(f"- {group} ({entry['tool_count']}){desc_part}")

        if models_summary:
            lines.append("")
            lines.append(models_summary)

        media_rules = self._build_media_rules(direct_vision)
        if media_rules:
            lines.append("")
            lines.append("# 多媒体处理")
            lines.append(media_rules)

        context_reading_rules = self._build_context_reading_rules()
        if context_reading_rules:
            lines.append("")
            lines.append(context_reading_rules)

        # 工具使用指引 + 计划前置 + 记忆使用提示（静态引导，归入 stable 层冻结复用）
        lines.append("")
        lines.append(_TOOL_USAGE_RULES)
        lines.append("")
        lines.append(_PLAN_USAGE_RULES)
        lines.append("")
        lines.append(_MEMORY_USAGE_HINT)

        lines.append("")
        lines.append(_PARALLEL_CALL_HINT)

        # 后台任务行为规范：仅子代理委托启用时注入（无后台任务来源则规则无意义）
        if _delegation_enabled():
            lines.append("")
            lines.append(_BACKGROUND_TASK_HINT)

        return [{"role": "system", "content": "\n".join(lines)}]

    @staticmethod
    def _build_context_reading_rules() -> str:
        """构建上下文解读和人物关系理解规则。"""
        return """# 对话上下文理解

## 消息标签
对话中的 [key:value] 标签含义：
- [uid:xxx] — 消息发送者的用户ID，同一uid是同一人
- [name:xxx] — 发送者用户名
- [nickname:xxx] — 发送者群内昵称
- [channel:xxx] — 消息来源频道标识（adapter_key），send_message 等频道工具的 channel_id 参数应填此值
- [session_id:xxx] — 会话ID（同一频道内会话上下文标识）
- [group_id:xxx] — 群组ID，不同group_id是不同群
- [message_id:xxx] — 当前这条消息的平台 ID；可用 lookup_message(message_id=xxx) 精确取回（含窗口外）
- [at_uid:xxx] — 消息中 @ 提及的用户ID
- [at_uid:all] — @ 全体成员
- [reply_to:xxx] — 引用回复：xxx 是被引用消息的 message_id。标签后常紧跟该消息的短预览（约 200 字）；预览不够或需要原文/前后文时，调用 lookup_message(message_id=xxx)

## 引用消息怎么用
- 看见 [reply_to:abc]张三: 你好… → 用户正在回复 id=abc 的消息；预览已内联时通常够用
- 预览缺失、被截断、或不在当前对话窗口 → lookup_message(message_id="abc") 取回被引用原文及邻接上下文
- 不要臆造被引用内容；查不到则如实说明（可能未入库或已清空）
- 语义翻旧账用 recall_conversation；按 ID 精确定位用 lookup_message

## 人物识别
- 以 uid 为准识别身份，name/nickname 可能变化
- 群聊中 [uid:xxx] 是这条消息的发送者
- [at_uid:xxx] 是消息中被 @ 的人的 uid
- 当 [at_uid:xxx] 中的 xxx 是你自己的 uid 时，表示有人在 @ 你，需要回应

## @ 提及用户
在 send_message 的 content 中使用 [at_uid:xxx] 可以 @ 提及用户：
- [at_uid:12345] — @ uid 为 12345 的用户
- [at_uid:all] — @ 全体成员
- 示例：看到 [uid:12345] 的消息，回复时写 [at_uid:12345] 即可 @ 该用户
- 不需要 @ 时直接写普通文本

## 回复方式
直接输出文字即可回复当前会话（系统自动投递）；需要 @ 提及、引用回复、
指定其他会话或发送媒体时再调用 send_message 等工具。"""

    @staticmethod
    def _build_media_rules(direct_vision: bool = False) -> str:
        """根据 EntityRegistry 中的 media:TYPE 标签动态生成媒体处理规则。

        文案对视觉/非视觉模型保持静态一致（图片规则同时覆盖两种情形），
        切换模型视觉能力不再改变 stable 层字节（缓存前缀稳定）；
        direct_vision 参数保留仅为调用方签名兼容。
        """
        tag_tool_map: dict[str, list[str]] = {}
        for entity in EntityRegistry.get_all():
            if entity.entity_type.value != "tool" or not entity.enabled:
                continue
            for tag in entity.tags:
                if tag.startswith("media:"):
                    media_type = tag[6:]
                    tag_tool_map.setdefault(media_type, []).append(entity.name)

        if not tag_tool_map:
            return ""

        lines = [
            "对话中出现 [media_type:类型][media_path:路径] 标签时，**必须优先使用下列内置媒体工具**处理：",
        ]
        for media_type, tool_names in sorted(tag_tool_map.items()):
            tools_str = " / ".join(tool_names)
            if media_type == "image":
                lines.append(
                    f"- [media_type:image] → 若图片已直接以视觉形式呈现则无需调用工具识别；"
                    f"未直接呈现或需更深入分析（OCR/细节）时调用 {tools_str}"
                )
            else:
                lines.append(f"- [media_type:{media_type}] → {tools_str}")
        lines.append(
            "禁止用 run_shell_command 编写脚本（如 python HTTP 请求）替代上述媒体工具——"
            "内置工具已封装好多模型回退，更可靠。"
        )
        lines.append("媒体分析是耗时操作，应与其他独立操作并行发起，避免阻塞对话。")
        return "\n".join(lines)

    # ==================================================================
    # LLM 上下文组装（Prompt 分层缓存架构）
    # ==================================================================

    def build_persona_block(
            self,
            persona_parts: List[str],
            static_guide: str = "",
    ) -> str:
        """构建人设块：人设 + 运行环境 + 静态指南。

        与工具无关——工具激活/发现变化不会使其失效，
        作为独立缓存块长期命中（它是 stable 前缀中最大最稳定的部分）。
        """
        parts = list(persona_parts)
        parts.append(_env_info_block())
        if static_guide:
            parts.append(static_guide)
        return "\n\n".join(parts)

    def build_tools_block(
            self,
            models_summary: str = "",
            direct_vision: bool = False,
    ) -> str:
        """构建工具块：工具使用规则 + 工具目录 + 媒体规则（随工具集变化重建）。"""
        parts: list[str] = []
        for msg in self.build_tool_system_prompt(
                models_summary=models_summary, direct_vision=direct_vision,
        ):
            if msg.get("content"):
                parts.append(msg["content"])
        return "\n\n".join(parts)

    def build_stable_layer(
            self,
            persona_parts: List[str],
            models_summary: str = "",
            direct_vision: bool = False,
            static_guide: str = "",
    ) -> str:
        """构建完整 stable 层（人设块 + 工具块拼接；分块缓存路径请分别调用两个 block 方法）。"""
        return "\n\n".join(p for p in (
            self.build_persona_block(persona_parts, static_guide),
            self.build_tools_block(models_summary, direct_vision),
        ) if p)

    def stable_fingerprint(self, models_summary: str = "", direct_vision: bool = False) -> str:
        """计算 stable 层动态输入的指纹（任一输入变化即触发重建）。

        覆盖：工具目录、可沉睡分组、工具规则、模型摘要、媒体规则、运行环境。
        不含激活状态（目录文案已静态化，激活状态由 exec_context 动态呈现）。
        以 _tools_version + 激活版本门控：工具集与激活状态未变时直接返回缓存哈希，
        跳过 json.dumps 开销（激活版本仍参与门控：schemas 成员随激活变化需重建检测）。
        """
        from agent.mind.tool_activation import tool_activation

        cache_key = (
            self._tool_assembly.tools_version,
            tool_activation.version, models_summary, direct_vision,
        )
        if self._fp_cache is not None and self._fp_cache[:4] == cache_key:
            return self._fp_cache[4]

        import json as _json

        from agent.mind.prompt_layers import prompt_cache_manager

        mc = _get_mind_config()
        rules = mc.tool_system_rules if hasattr(mc, "tool_system_rules") else []
        catalog = EntityRegistry.get_entity_catalog()
        sleepable = EntityRegistry.get_sleepable_groups()
        # 指纹不含激活状态：目录文案已静态化，激活状态不再影响 stable 内容
        result = prompt_cache_manager.compute_hash(
            _json.dumps(catalog, sort_keys=True, ensure_ascii=False),
            _json.dumps(sleepable, sort_keys=True, ensure_ascii=False),
            "\n".join(rules),
            models_summary,
            self._build_media_rules(direct_vision),
            str(_delegation_enabled()),
            _env_info_block(),
        )
        self._fp_cache = (cache_key[0], cache_key[1], cache_key[2], cache_key[3], result)
        return result

    async def build_llm_context(
            self,
            *,
            persona_text: str = "",
            tools_text: str = "",
            context_text: str = "",
            memory_msgs: List[Dict],
            anything: Optional["Everything"] = None,
            adapter_key: str = "",
            target_id: str = "",
            models_summary: str = "",
            prefetched_conversation: Optional[List[Dict]] = None,
            scope: str = "",
            profile_msgs: Optional[List[Dict]] = None,
            relation_msgs: Optional[List[Dict]] = None,
            goal_msgs: Optional[List[Dict]] = None,
            summary_row: Optional[Dict] = None,
            status_text: str = "",
    ) -> List[Dict]:
        """组装完整 LLM 上下文（声明式管线），每次调用实时从 DB 获取最新对话历史。

        各内容块的顺序由 @context_block 声明的变动率决定（值越大变动越频繁，
        排越靠后，见 context_pipeline）：
        stable(0) → context(10) → summary(20) → conversation(30) →
        status/profile/volatile/memory/provider(40+) → overflow/security(50+)
        tail_injection 关闭时经变动率覆盖表回退旧布局（动态在历史之前）。

        Args:
            prefetched_conversation: 外部已获取的对话历史（避免重复拉取）。
                若为 None，内部自动从 DB 获取。
            profile_msgs: 实体画像消息（每实体一条，与 memory_msgs 分离放置）。
            summary_row: 对话摘要行（{summary, watermarks, folded_count}）。
            status_text: 记忆状态区块文本（心跳维护，尾部动态区注入）。
        """
        inp = ContextInput(
            persona_text=persona_text,
            tools_text=tools_text,
            context_text=context_text,
            status_text=status_text,
            memory_msgs=memory_msgs,
            profile_msgs=profile_msgs or [],
            relation_msgs=relation_msgs or [],
            goal_msgs=goal_msgs or [],
            summary_row=summary_row,
            anything=anything,
            adapter_key=adapter_key,
            scope=scope,
            prefetched_conversation=prefetched_conversation,
        )
        pipeline = self._pipeline if _tail_injection_enabled() else self._pipeline_legacy
        all_msgs = await pipeline.build(inp)

        # 确保最后一条非 system 消息不是 assistant 角色，防止 Anthropic prefill 400 错误。
        # （规则实现已收拢至 message_schema.fix_trailing_assistant，此处就地委托）
        from agent.mind.message_schema import fix_trailing_assistant
        fix_trailing_assistant(all_msgs)

        return all_msgs

    # ==================================================================
    # 上下文内容块（@context_block 声明，管线按变动率从静到动组装）
    # ==================================================================

    @context_block("stable", VOL_STABLE, "人设 + 工具提示 + 静态指南（stable 层）")
    def _blk_persona(self, inp: ContextInput) -> List[Dict]:
        """人设块：人设 + 环境 + 静态指南（最稳定的前缀段，断点1）。"""
        if not inp.persona_text:
            return []
        return [{"role": "system", "content": inp.persona_text}]

    @context_block("stable", VOL_STABLE)
    def _blk_tools(self, inp: ContextInput) -> List[Dict]:
        """工具块：工具目录 + 使用规则 + 媒体规则（断点2）。"""
        if not inp.tools_text:
            return []
        return [{"role": "system", "content": inp.tools_text}]

    @context_block("context", VOL_TAIL_HEAD, "动态便签 + 文件索引（尾部动态区最前）")
    def _blk_notes(self, inp: ContextInput) -> List[Dict]:
        """context 层：动态便签 + 文件索引（尾部动态区最稳定的内容，放最前）。

        不放前缀锚点位：心跳任务/技能评审/记忆写入都会改便签与文件索引，
        若置于历史之前，每次漂移会让其后 20-40K 的摘要+对话前缀缓存整体
        失效（空闲后首轮命中率跌到稳定层量级）；放尾部则漂移只损尾部增量，
        内容经 PromptCacheManager 内容寻址缓存，未变时字节级稳定。
        """
        if not inp.context_text:
            return []
        return [{"role": "system", "content": inp.context_text}]

    @context_block("summary", VOL_PERIODIC, "早期对话摘要（折叠周期内固定）")
    def _blk_summary(self, inp: ContextInput) -> List[Dict]:
        """对话摘要块：折叠周期内字节固定，历史前缀的缓存锚点。"""
        row = inp.summary_row
        if not row or not row.get("summary"):
            return []
        folded = int(row.get("folded_count", 0) or 0)
        dropped = int(row.get("dropped_count", 0) or 0)
        dropped_note = (
            f"\n- 另有 {dropped} 条因摘要生成失败未纳入上方摘要（仍可用上述检索取回）"
            if dropped else ""
        )
        return [{
            "role": "system",
            "content": (
                f"[早期对话摘要] 以下是本对话更早内容的摘要（共 {folded} 条已折叠，"
                "摘要随对话推进周期性更新）：\n"
                f"{row['summary']}\n"
                "- 窗口外原文仍完整存于数据库：recall_conversation 按语义检索，"
                "lookup_message 按 message_id 精确取回"
                f"{dropped_note}"
            ),
        }]

    @context_block("conversation", VOL_HISTORY, "对话历史（原始窗口）")
    async def _blk_conversation(self, inp: ContextInput) -> List[Dict]:
        """对话历史原始窗口（水位线后纯追加；实时从 DB 获取，不可缓存）。

        多轮 think_loop 期间用户可能发送新消息，必须确保拿到最新对话；
        调用方预取时直接复用（避免同一次 get_recollection 内重复拉取）。
        """
        conversation_list: List[Dict] = []
        max_size = 0
        if inp.prefetched_conversation is not None:
            conversation_list = inp.prefetched_conversation
            if self._conversation_data:
                max_size = self._conversation_data.max_size
        elif self._conversation_data and inp.anything:
            max_size = self._conversation_data.max_size
            conversation_list = await self._conversation_data.get_conversation_record_by_everything(
                inp.anything,
            )
            log(f"对话历史: {len(conversation_list)} 条 (窗口上限 {max_size})", "DEBUG", tag="PFC")

        # 会话令牌：为历史消息包裹可信标记（防 prompt 注入伪造历史）
        try:
            from agent.security.session_token import current_token, wrap_history_content
            if current_token():
                conversation_list = [
                    {**m, "content": wrap_history_content(m["content"])}
                    if isinstance(m.get("content"), str) else m
                    for m in conversation_list
                ]
        except Exception as exc:
            # 安全标记包裹失败 = 本轮历史无防注入保护，必须可见
            log(f"会话令牌包裹失败（本轮历史无防伪标记）: {exc}", "WARNING", tag="安全")

        # 写入中间态供 overflow 块使用
        inp.conversation_list = conversation_list
        inp.max_conversation_size = max_size
        return conversation_list

    @context_block("status", VOL_SESSION, "记忆系统状态（心跳维护）")
    def _blk_status(self, inp: ContextInput) -> List[Dict]:
        """记忆状态区块（心跳维护，周期性变化）：尾部动态区独立注入。"""
        if not inp.status_text:
            return []
        return [{"role": "system", "content": inp.status_text}]

    @context_block("profile", VOL_SESSION + 1, "实体画像注入")
    def _blk_profile(self, inp: ContextInput) -> List[Dict]:
        """实体画像（每实体一条，动态区中最稳定，放最前）。"""
        return list(inp.profile_msgs)

    @context_block("relation", VOL_SESSION + 2, "关系网络注入")
    def _blk_relation(self, inp: ContextInput) -> List[Dict]:
        """关系网络快照（当前会话相关实体的已知关系，随实体集合低频变）。"""
        return list(inp.relation_msgs)

    @context_block("goals", VOL_SESSION + 2, "活跃目标注入")
    def _blk_goals(self, inp: ContextInput) -> List[Dict]:
        """活跃目标快照（仅目标 CRUD 时字节变化，对话轮次间完全稳定）。"""
        return list(inp.goal_msgs)

    @context_block("volatile", VOL_SESSION + 2, "短期记忆（volatile 层）")
    def _blk_volatile(self, inp: ContextInput) -> List[Dict]:
        """短期记忆桶（角色按存储原样使用，主流格式不做转换）。"""
        clips = list(self._work_memory.get_temporary(inp.scope))
        if not clips:
            return []
        header = {
            "role": "system",
            "content": "[短期记忆] 以下是临时提醒区内容（任务指令/定时提醒/系统推送等，按顺序对应索引 0 起）；"
                       "对应事项完成后用 remove_short_term_memory 清理，避免堆积。",
        }
        return [header] + clips

    @context_block("memory", VOL_SESSION + 3, "语义召回 + 跨频道 + 技能匹配")
    def _blk_memory(self, inp: ContextInput) -> List[Dict]:
        """语义召回 + 跨频道 + 技能注入（每会话基于最新对话重建）。"""
        return list(inp.memory_msgs)

    @context_block("provider", VOL_SESSION + 4, "上下文提供者注入")
    async def _blk_provider(self, inp: ContextInput) -> List[Dict]:
        """上下文提供者注入（实体自驱数据，滞后一轮的后台快照）。"""
        try:
            from core.context_provider import ContextProviderRegistry
            snippets, _provider_metrics = await ContextProviderRegistry.collect(inp.scope)
            return [{"role": "system", "content": s} for s in snippets]
        except Exception as exc:
            log(f"上下文提供者收集失败: {exc}", "DEBUG", tag="PFC")
            return []

    @context_block("overflow", VOL_MESSAGE, "上下文溢出提示")
    async def _blk_overflow(self, inp: ContextInput) -> List[Dict]:
        """上下文溢出提示：窗口满时告知窗口外数量与检索路径。

        软归档感知（窗口外消息完整存于 DB）；摘要窗口开启时已折叠部分
        由摘要块覆盖，口径需扣除避免与摘要块矛盾。
        """
        max_size = inp.max_conversation_size
        conversation_list = inp.conversation_list
        if not (max_size > 0 and len(conversation_list) >= max_size):
            return []
        if self._conversation_data is None or inp.anything is None:
            return []
        hidden = 0
        try:
            total = await self._conversation_data.count_messages(inp.anything)
            hidden = max(0, total - len(conversation_list))
        except Exception as exc:
            log(f"窗口外消息计数失败: {exc}", "DEBUG", tag="PFC")
        folded = int((inp.summary_row or {}).get("folded_count", 0) or 0)
        uncovered = max(0, hidden - folded)
        if folded:
            hidden_note = (
                f"，另有 {uncovered} 条未覆盖消息在窗口外"
                f"（更早的 {folded} 条已折叠为上方摘要）" if uncovered
                else f"（更早的 {folded} 条已折叠为上方摘要）"
            )
        else:
            hidden_note = f"，另有 {hidden} 条更早消息在窗口外" if hidden else ""
        return [{"role": "system", "content": (
            f"[上下文溢出] 当前仅显示最近 {max_size} 条对话{hidden_note}。\n"
            "- 可通过 recall_conversation 按语义搜索窗口外的对话内容\n"
            "- 看到 [reply_to:xxx] / 已知 message_id 时，用 lookup_message 精确取回该条（含窗口外）\n"
            "- 建议使用 memorize 将对话中的重要信息存入长期记忆，避免遗忘\n"
            "- 可通过 recall 检索长期记忆中的相关信息"
        )}]

    @context_block("security", VOL_MESSAGE + 1, "会话令牌安全标记")
    def _blk_security(self, inp: ContextInput) -> List[Dict]:
        """会话令牌规则提示（防注入伪造历史；默认关闭，开启时注入）。"""
        try:
            from agent.security.session_token import build_token_rule_hint, current_token
            if not current_token():
                return []
            hint = build_token_rule_hint()
            return [{"role": "system", "content": hint}] if hint else []
        except Exception as exc:
            log(f"令牌提示构建失败: {exc}", "DEBUG", tag="安全")
            return []

    def _build_scene_info(
        self,
        anything: Optional["Everything"],
        adapter_key: str = "",
    ) -> str:
        """构建当前对话场景信息（私聊/群聊、群组ID、频道、发送者等）。"""
        if not anything:
            return ""

        parts: list[str] = []

        group_id = getattr(anything, "group_id", None)
        uid = getattr(anything, "uid", None)
        channel_key = adapter_key or getattr(anything, "adapter_key", "")

        if group_id and group_id not in (0, "0", ""):
            parts.append(f"群聊 group_id={group_id}")
            senders = self._work_memory.get_group_recent_senders(_safe_entity_scope(anything))
            if senders:
                desc = ", ".join(f"uid:{s[0]}({s[1]})" for s in senders if s[0])
                if desc:
                    parts.append(f"待回复消息来自: {desc}")
        elif uid and uid not in (0, "0", ""):
            parts.append(f"私聊 uid={uid}")

        if channel_key:
            parts.append(f"频道={channel_key}")

        if not parts:
            return ""

        return f"[当前场景] {' | '.join(parts)}"

    # ==================================================================
    # 执行上下文
    # ==================================================================

    def build_execution_context(
            self,
            execution_steps: list[str],
            start_time: float,
            iteration: int,
            *,
            adapter_key: str = "",
            safety_limit: int = 0,
            anything: Optional["Everything"] = None,
            budget_hint: str = "",
    ) -> dict:
        """构建当前轮次的执行状态消息（轮次、耗时、工具态势、频道、历史步骤、待处理消息）。

        budget_hint：上下文预算提醒文本（think_loop 按上轮真实用量计算），
        非空时追加到本轮 exec_context。
        """
        import time
        elapsed = time.time() - start_time
        remaining = (safety_limit - iteration) if safety_limit > 0 else None

        wm = self._work_memory
        ta = self._tool_assembly
        lines: list[str] = []

        # 当前对话场景信息
        scene_info = self._build_scene_info(anything, adapter_key)
        if scene_info:
            lines.append(scene_info)

        if iteration == 0:
            limit_hint = f"最多 {safety_limit} 轮" if safety_limit > 0 else ""
            lines.append(f"[系统提示] 新一轮对话开始 | 请仔细分析上下文后决定操作{' | ' + limit_hint if limit_hint else ''}")
        else:
            round_info = f"第 {iteration + 1} 轮"
            if remaining is not None:
                round_info += f" | 剩余 {remaining} 轮"
            round_info += f" | 已耗时 {elapsed:.2f}秒"
            lines.append(f"[系统提示] {round_info}")

            # 剩余轮次警告（动态强度）
            if remaining is not None:
                if remaining == 1:
                    lines.append(_FINAL_ROUND_WARNING)
                elif remaining == 2:
                    lines.append(_URGENT_ROUND_WARNING)
                elif remaining <= safety_limit // 2:
                    lines.append(
                        f"[轮次提醒] 已用 {iteration + 1}/{safety_limit} 轮，"
                        "建议优先完成核心操作，不必要的步骤可跳过。"
                    )

        # 工具态势摘要（名单截断展示： exec_context 每轮重建，全量名单是纯增量 token）
        tool_parts: list[str] = []
        if ta.tag_activated_tools:
            tool_parts.append(f"标签激活: {_cap_names(sorted(ta.tag_activated_tools))}")
        if ta.discovered_tools:
            tool_parts.append(f"动态发现: {_cap_names(sorted(ta.discovered_tools))}")
        hot = ta.get_hot_tool_names()[:5]
        if hot:
            tool_parts.append(f"热工具: {', '.join(hot)}")
        if tool_parts:
            lines.append(f"[工具态势] {' | '.join(tool_parts)}")

        # 目标 nag 提醒（对齐 Claude Code todo_reminder：10 轮未更新才提醒）
        try:
            from agent.mind.tool_activation import ToolActivationManager
            from agent.planning.nag import maybe_nag
            nag_text = maybe_nag(ToolActivationManager.current_scope())
            if nag_text:
                lines.append(nag_text)
        except Exception:
            log("build_execution_context 异常已忽略", "DEBUG")

        # 上下文预算提醒（逼近压缩阈值时让模型主动收敛）
        if budget_hint:
            lines.append(budget_hint)

        # 沉睡分组激活状态（剩余最后一轮时提示续期）
        from agent.mind.tool_activation import tool_activation
        active_groups = tool_activation.active_groups()
        if active_groups:
            group_desc = ", ".join(f"{g}(剩余{r}轮)" for g, r in sorted(active_groups.items()))
            lines.append(f"[已激活工具分组] {group_desc}")
            expiring = [g for g, r in active_groups.items() if r <= 1]
            if expiring:
                lines.append(
                    f"⚠️ 分组 {', '.join(expiring)} 即将回到沉睡，"
                    "如下轮仍需使用请立即调用 activate_tool_group 续期。"
                )

        # 频道信息
        if adapter_key and self._channel_manager:
            channel = self._channel_manager.get(adapter_key)
            if channel:
                info = channel.get_status_info()
                cap_count = len(info.get("capabilities", []))
                lines.append(f"[当前频道] {adapter_key} ({info.get('name', '?')}) | {cap_count} 项能力")

        # 当前模型（动态呈现；stable 层的可用模型清单不再标注默认项，
        # 避免 switch_model 改变 stable 字节击穿缓存前缀）
        try:
            from agent.llm import get_llm_manager
            current_model = get_llm_manager().get_current_model_id()
            if current_model:
                lines.append(f"[当前模型] {current_model}")
        except Exception:
            log("当前模型信息获取失败", "DEBUG", tag="PFC")

        # 短期记忆状态（本 scope 桶）
        temp_count = len(wm.get_temporary(_safe_entity_scope(anything)))
        if temp_count:
            lines.append(f"[短期记忆] {temp_count}/{wm.max_temp} 条")

        if execution_steps:
            lines.append("[已完成步骤（以下操作已执行成功，请勿重复）]")
            lines.extend(execution_steps)

        pending = wm.peek_all_tasks()
        if pending:
            current = _safe_entity_scope(anything)
            lines.append(f"[会话通知] {len(pending)} 个会话有新消息待处理：")
            for scope, _uid, _gid, preview in pending[:5]:
                unread = wm.get_unread_count(scope)
                label = _format_scope_label(scope, wm.get_adapter_key(scope))
                marker = "（当前会话）" if scope == current else ""
                unread_text = f"{unread} 条未读: " if unread > 0 else ""
                lines.append(f"  • {label}{marker} — {unread_text}{preview[:80]}")
            if len(pending) > 5:
                lines.append(f"  • ...还有 {len(pending) - 5} 个会话")
            lines.append(_SESSION_NOTIFY_HINT)
            lines.append(_PENDING_HINT)
        else:
            lines.append(_NO_PENDING_HINT)

        return {"role": "system", "content": "\n".join(lines), "_layer": "exec_context"}
