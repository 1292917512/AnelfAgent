"""聊天服务 -- 消息发送、历史加载、bot 名称获取、计划取消与中断。"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from core.path import ConfigPaths
from services._runtime import get_agent_app, get_runtime, is_ready

UPLOAD_DIR = Path(ConfigPaths.UPLOAD_DIR).resolve()

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".amr", ".opus"}
_VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv"}

# 消息内容内部标签（[tag:xxx]）剥离正则，历史清洗与会话标题共用；
# 键为单词字符（与 tag_label 生成一致），值禁止跨 [、] 与换行，
# 防止多行正文（执行摘要等）被错误配对吞掉
_TAG_PREFIX_RE = re.compile(r"\[(?:\w+):([^\[\]\n]*)\]")


def normalize_web_scope_id(scope_id: str) -> str:
    """webui 历史查询的 scope_id 归一化：裸 user_id 自动补 adapter 前缀。"""
    sid = (scope_id or "").strip()
    if not sid:
        return "webui:web_user"
    if ":" in sid.split("#", 1)[0]:
        return sid
    return f"webui:{sid}"


def clean_message_for_display(msg: Dict[str, Any]) -> Dict[str, Any]:
    """清理消息中的内部标签，返回干净的前端展示数据。

    清洗顺序：元数据标签（time/uid 等）与功能性标签（media_file 等）整段删除
    ——保留值只会拼出乱码前缀；其余 [k:v] 标签保留值（兼容旧语义）。

    kind 标记供前端结构化渲染：
    - tool_summary：工具执行记录 → 折叠工具卡片（附 summary 结构化条目）
    - system_notice：[系统]/[执行步骤] 等系统元消息 → 居中细条
    """
    from core.tags import strip_functional_tags, strip_message_meta_tags

    content = str(msg.get("content", ""))
    content = strip_message_meta_tags(content)
    content = strip_functional_tags(content)
    # 工作区上下文注入块剥离（注入文本不在历史里重复刷屏，只留用户原文）
    from services.workspace_context import strip_workspace_context
    content = strip_workspace_context(content)
    # kind 判定先于通用标签剥离：结构化前缀一旦识别即锁定，
    # 避免正文中的类标签片段干扰后续清洗导致前缀丢失
    head = content.strip()
    kind: Optional[str] = None
    if head.startswith("[已执行操作摘要]"):
        kind = "tool_summary"
    elif head.startswith(("[系统]", "[执行步骤]")):
        kind = "system_notice"
    content = _TAG_PREFIX_RE.sub(r"\1", content).strip()
    result: Dict[str, Any] = {
        "role": msg.get("role", ""),
        "content": content,
    }
    if kind:
        result["kind"] = kind
    if kind == "tool_summary":
        from agent.mind.tools.reply_finalize import parse_execution_summary
        summary = parse_execution_summary(content)
        if summary is not None:
            result["summary"] = summary
    if "id" in msg:
        result["id"] = msg["id"]
    ts_ns = msg.get("ts_ns")
    if ts_ns and isinstance(ts_ns, (int, float)) and ts_ns > 0:
        ts = ts_ns / 1e9 if ts_ns > 1e15 else ts_ns
        # ts：epoch 秒（前端时间线合排 plan/delegation 卡片用，须与消息同源）
        result["ts"] = ts
        result["timestamp"] = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    return result


def classify_file_type(ext: str) -> str:
    """按扩展名分类上传文件类型（image/audio/video/file）。"""
    ext = ext.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _VIDEO_EXTS:
        return "video"
    return "file"


def resolve_media_path(file_path: str) -> str:
    """解析媒体路径：相对路径优先按当前路径，其次按工作区根目录解析。"""
    if not file_path or file_path.startswith(("http://", "https://", "/api/")):
        return file_path
    if os.path.isabs(file_path) or os.path.exists(file_path):
        return file_path
    try:
        from services.filesystem import safe_workspace_path
        resolved = safe_workspace_path(file_path)
        if os.path.exists(resolved):
            return resolved
    except Exception:
        log("resolve_media_path 异常已忽略", "DEBUG")
    return file_path


class ChatService:

    def is_ready(self) -> bool:
        return is_ready()

    async def load_history(
        self, scope_id: str = "webui:web_user", limit: int = 50,
        before_id: Optional[int] = None,
    ) -> List[dict]:
        """加载指定用户的历史会话记录（scope_id 含 adapter 前缀，如 webui:web_user）。

        before_id：分页游标，仅取 id 早于该值的消息（"加载更早"向前翻页）。
        """
        rt = get_runtime()
        if rt is None:
            return []
        return await rt.data_center.sqlite.fetch_conversation_with_id(
            scope_type="user", scope_id=scope_id, limit=limit, before_id=before_id,
        )

    async def list_chats(self, user_id: str) -> List[Dict[str, Any]]:
        """列出该用户在 webui 频道下出现过的所有 chat_id（基于消息表去重）。

        会话标题取最近一条用户消息，与历史清洗同规则剥离元数据/功能标签。
        """
        rt = get_runtime()
        if rt is None:
            return []
        sessions = await rt.data_center.sqlite.list_user_chat_sessions(
            normalize_web_scope_id(user_id)
        )
        chats: List[Dict[str, Any]] = []
        for s in sessions:
            sid = s["scope_id"]
            # scope_id 形如 "webui:web_user" 或 "webui:web_user#abc123"
            chat_id = sid.split("#", 1)[1] if "#" in sid else "default"
            title = "新会话"
            raw_content = s.get("last_user_content")
            if raw_content is not None:
                title = clean_message_for_display({"content": str(raw_content)})["content"][:40] or "(空消息)"
            chats.append({
                "chat_id": chat_id,
                "scope_id": sid,
                "title": title,
                "last_ts": s["last_ts"],
                "message_count": s["message_count"],
            })
        return chats

    async def send_message(
        self,
        text: str,
        *,
        images: Optional[list] = None,
        media_segments: Optional[list] = None,
        user_id: str = "web_user",
        user_name: str = "用户",
        chat_id: Optional[str] = None,
        adapter_key: str = "webui",
    ) -> None:
        """通过 AgentApp 发送一条消息。

        Args:
            chat_id: 前端多会话标识；非空时写入 ``Everything.session_id``，
                参与 ``entity_scope`` 计算实现同 uid 多会话隔离。
        """
        app = get_agent_app()
        if app is None:
            raise RuntimeError("AgentApp 尚未初始化")
        await app.send_message(
            user_id=user_id,
            content=text,
            user_name=user_name,
            to_me=True,
            images=images or None,
            media_segments=media_segments or None,
            adapter_key=adapter_key,
            session_id=chat_id or "",
        )

    async def send_web_message(
        self,
        message: str,
        *,
        images: Optional[List[str]] = None,
        files: Optional[List[str]] = None,
        user_id: str = "web_user",
        user_name: str = "用户",
        chat_id: Optional[str] = None,
    ) -> None:
        """组装并发送 WebUI 聊天消息（图片/文件附件 → ImageContent/MessageSegment）。"""
        from agent.channel.schemas import MessageSegment, SegmentType
        from agent.llm.types import ImageContent

        image_contents: Optional[List[Any]] = None
        if images:
            from core.path import parse_upload_url
            image_contents = []
            for img in images:
                # 可服务 URL 反解为本地路径（URL 规则单点定义在 core.path）
                parsed = parse_upload_url(img)
                if parsed is not None:
                    local = str(UPLOAD_DIR / parsed[0] / parsed[1])
                    if Path(local).exists():
                        img = local
                if img.startswith("http"):
                    image_contents.append(ImageContent(data=img, is_url=True))
                else:
                    image_contents.append(ImageContent(data=img))

        media_segments: Optional[List[Any]] = None
        if files:
            media_segments = []
            for file_path in files:
                file_path = resolve_media_path(file_path)
                ext = Path(file_path).suffix.lower()
                ftype = classify_file_type(ext)
                seg_type_map = {
                    "image": SegmentType.IMAGE,
                    "audio": SegmentType.AUDIO,
                    "video": SegmentType.VIDEO,
                    "file": SegmentType.FILE,
                }
                seg = MessageSegment(
                    type=seg_type_map.get(ftype, SegmentType.FILE),
                    file_path=file_path,
                    file_name=Path(file_path).name,
                    url=file_path if file_path.startswith("/api/") else "",
                )
                if ftype == "image":
                    if image_contents is None:
                        image_contents = []
                    image_contents.append(ImageContent(data=file_path, is_url=False))
                else:
                    media_segments.append(seg)

        text = message
        if files:
            file_descs = [f"[{classify_file_type(Path(fp).suffix.lower())}:{fp}]" for fp in files]
            if file_descs:
                text = text + "\n" + " ".join(file_descs) if text else " ".join(file_descs)

        # 工作区上下文注入（打开文件/选区/标签页 → 消息前缀块；历史清洗剥离）
        from entities.ui.tools import get_ui_state_snapshot
        from services.workspace_context import inject_workspace_context
        try:
            text = inject_workspace_context(text, get_ui_state_snapshot())
        except Exception:
            pass  # 注入失败不阻塞发送

        await self.send_message(
            text,
            images=image_contents,
            media_segments=media_segments if media_segments else None,
            user_id=user_id,
            user_name=user_name,
            chat_id=chat_id,
            adapter_key="webui",
        )

    @staticmethod
    def scope_for_chat(chat_id: str) -> str:
        """chat_id → entity scope（webui 频道固定用户维度的构造规则）。"""
        from agent.planning.tracker import make_scope
        return make_scope("webui:web_user", "" if chat_id == "default" else chat_id)

    async def cancel_plan(self, chat_id: str, plan_id: str) -> bool:
        """取消计划：标记 cancelled + interrupt scope + 发射事件。

        状态机逻辑由 ``agent.planning.tracker.cancel_plan`` 统一实现，
        本方法只做参数组装（chat_id → scope）。
        """
        from agent.planning import tracker as plan_tracker
        scope = plan_tracker.make_scope(
            "webui:web_user", "" if chat_id == "default" else chat_id,
        )
        return await plan_tracker.cancel_plan(scope, plan_id, reason="用户取消")

    def interrupt_chat(self, chat_id: str) -> Dict[str, Any]:
        """协作式中断当前回复 + 取消该会话运行中的子代理。"""
        rt = get_runtime()
        if rt is None:
            return {"status": "error", "error": "runtime 未就绪"}
        scope = self.scope_for_chat(chat_id)
        interrupted = rt.mind.interrupt(scope, reason="用户点击停止生成")
        cancelled = 0
        dm = getattr(rt.mind, "delegation_manager", None)
        if dm is not None:
            cancelled = dm.cancel_scope(scope)
        if not interrupted and cancelled == 0:
            return {"status": "idle"}
        return {"status": "ok", "interrupted": interrupted, "cancelled_delegations": cancelled}

    def list_delegations(self, chat_id: str) -> List[Dict[str, Any]]:
        """列出该会话运行中的子代理委托。"""
        from services.delegation import DelegationService
        return DelegationService().running_for_scope(self.scope_for_chat(chat_id))

    def cancel_delegation(self, delegation_id: str) -> Optional[bool]:
        """取消运行中的子代理委托。

        Returns:
            True/False 表示取消结果；None 表示 runtime 未就绪。
        """
        from services.delegation import DelegationService
        return DelegationService().cancel(delegation_id)

    def register_output(self, output: Any, adapter_key: str = "webui") -> None:
        """将一个轻量频道注册到 ChannelManager。"""
        rt = get_runtime()
        if rt is None:
            raise RuntimeError("AgentRuntime 尚未初始化")
        if hasattr(output, "channel_id"):
            from agent.channel import get_channel_manager
            cm = get_channel_manager()
            if output.channel_id not in cm.list_channels():
                cm.register_lightweight(output)

    @staticmethod
    def get_bot_name() -> str:
        """从人设配置读取 bot 名称。"""
        try:
            from agent.config import get_config_provider
            data = get_config_provider().get_persona_config()
            if data.get("name"):
                return data["name"]
            # 启发式兜底：在人设文本行中查找"名称"字样，取其后的内容作为 bot 名。
            # 该解析依赖人设文本的自然语言书写格式，较为脆弱——任意一步匹配
            # 失败都会落到默认名 "Bot"，不影响主流程。
            for line in data.get("personality", []):
                if "名称" in line:
                    for sep in ("：", ":"):
                        if sep in line:
                            parts = line.split(sep)
                            for i, pt in enumerate(parts):
                                if "名称" in pt and i + 1 < len(parts):
                                    name = parts[i + 1].split(",")[0].split("，")[0].strip()
                                    if name:
                                        return name
        except Exception as e:
            log(f"获取 bot 名称失败: {e}", "DEBUG")
        return "Bot"
