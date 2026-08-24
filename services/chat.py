"""聊天服务 -- 消息发送、历史加载、bot 名称获取、计划取消与中断。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from core.path import ConfigPaths
from services._runtime import get_agent_app, get_runtime, is_ready

UPLOAD_DIR = Path(ConfigPaths.UPLOAD_DIR).resolve()

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".amr", ".opus"}
_VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv"}


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
            image_contents = []
            for img in images:
                # Convert API URL back to local path for consistent path-based handling
                if img.startswith("/api/chat/files/"):
                    parts = img.replace("/api/chat/files/", "").split("/", 1)
                    if len(parts) == 2:
                        local = str(UPLOAD_DIR / parts[0] / parts[1])
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
        rt = get_runtime()
        if rt is None:
            return []
        dm = getattr(rt.mind, "delegation_manager", None)
        return dm.running_snapshot(self.scope_for_chat(chat_id)) if dm is not None else []

    def cancel_delegation(self, delegation_id: str) -> Optional[bool]:
        """取消运行中的子代理委托。

        Returns:
            True/False 表示取消结果；None 表示 runtime 未就绪。
        """
        rt = get_runtime()
        if rt is None:
            return None
        dm = getattr(rt.mind, "delegation_manager", None)
        return dm.cancel(delegation_id) if dm is not None else False

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
