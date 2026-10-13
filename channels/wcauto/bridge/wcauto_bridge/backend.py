"""桥后端 — wechatauto 包装层。

惰性导入 wechatauto（仅 Windows + 已登录微信环境可用），无微信环境下
模块可正常导入，RPC 调用返回明确错误而不是 ImportError 崩溃。

线程模型：wechatauto 全部 API 均为同步阻塞；Listener 回调在库内工作线程
执行；server 侧经 run_in_executor 调用 handle_rpc。所有 GUI 写操作
（发送/at/撤回/朋友圈互动/转发/标签群发）经 _gui_lock 串行化，
与 wechatauto 内部 uilock 同向，不引入第二把顺序源。
"""

from __future__ import annotations

import base64
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import protocol
from .config import BridgeConfig


class BridgeError(Exception):
    """桥后端可预期失败（未装依赖/窗口不可见/媒体过大等），message 透传给频道。"""


def _lazy_wechatauto():
    """惰性导入 wechatauto；不可用时抛 BridgeError。"""
    try:
        import wechatauto  # noqa: F401
        from wechatauto.db import Listener, WeChatDB
        from wechatauto.media import MediaDownloader
        return wechatauto, WeChatDB, Listener, MediaDownloader
    except Exception as exc:
        raise BridgeError(
            f"wechatauto 不可用（桥须运行在已登录微信 4.x 的 Windows 上）: {exc}"
        ) from exc


class _RecallGuardHook:
    """RecallGuard 子类化钩子：撤回事件落库的同时向频道推送。"""

    def __init__(self, guard_cls: Any, db: Any, mirror_dir: str,
                 emit: Callable[[str, Dict[str, Any]], None]) -> None:
        self._guard = guard_cls(db, mirror_dir=mirror_dir or None)
        self._emit = emit

    def watch(self, listener: Any) -> None:
        original = self._guard._on_revoke

        def _patched(msg: dict) -> None:
            original(msg)
            try:
                chat = msg.get("username") or ""
                original_content = ""
                original_media = ""
                try:
                    rows = self._guard.get_recalled(chat=chat or None, limit=1)
                    if rows:
                        latest = rows[0]
                        original_content = str(
                            latest.get("original_content") or "")[:500]
                        original_media = str(
                            latest.get("original_media_path") or "")
                except Exception:
                    pass
                self._emit(protocol.EVENT_RECALL, {
                    "chat": chat,
                    "revoker": self._guard._extract_revoker(str(msg.get("content", "")))
                               or msg.get("sender_username") or "",
                    "content": str(msg.get("content", ""))[:500],
                    "original_content": original_content,
                    "original_media_path": original_media,
                    "create_time": msg.get("create_time") or int(time.time()),
                })
            except Exception:
                pass

        self._guard._on_revoke = _patched
        self._guard.watch(listener, users=None, backfill=20)

    def get_recalled(self, chat: Optional[str], limit: int) -> Any:
        return self._guard.get_recalled(chat=chat or None, limit=limit)

    def close(self) -> None:
        self._guard.close()


class WeChatBackend:
    """微信后端：读侧（DB 监听）常驻，写侧（GUI）按需惰性初始化。"""

    def __init__(self, config: BridgeConfig,
                 emit: Callable[[str, Dict[str, Any]], None]) -> None:
        self._cfg = config
        self._emit = emit
        self._started_at = time.time()
        self._db: Any = None
        self._listener: Any = None
        self._media: Any = None
        self._recall: Optional[_RecallGuardHook] = None
        self._gui: Any = None
        self._gui_err: str = ""
        self._wx: Any = None
        self._gui_lock = threading.RLock()
        self._nick_cache: Dict[str, Any] = {}
        self._self_info: Dict[str, Any] = {}
        self._downloads_dir = ""

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动读侧：解密库 + 全局监听 + 防撤回。GUI 写侧不在这里初始化。"""
        _, WeChatDB, Listener, MediaDownloader = _lazy_wechatauto()
        cfg = self._cfg
        self._db = WeChatDB(account=cfg.account) if cfg.account else WeChatDB()
        self._self_info = dict(self._db.get_self_info() or {})
        self._media = MediaDownloader(self._db)
        self._downloads_dir = os.path.join(self._data_dir(), "downloads")
        os.makedirs(self._downloads_dir, exist_ok=True)

        self._listener = Listener(self._db, interval=1.0)
        self._listener.add_all(self._on_message)
        if cfg.enable_recall_guard:
            try:
                from wechatauto.recall import RecallGuard
                self._recall = _RecallGuardHook(
                    RecallGuard, self._db, cfg.recall_mirror_dir, self._emit)
                self._recall.watch(self._listener)
            except Exception as exc:
                self._emit(protocol.EVENT_STATUS, {
                    "state": "recall_guard_unavailable", "detail": str(exc)[:300]})
        self._listener.start()
        self._emit(protocol.EVENT_STATUS, {
            "state": "listener_started",
            "account": self._self_info.get("username", ""),
            "nickname": self._self_info.get("nick_name", ""),
        })

    def stop(self) -> None:
        """停止监听与防撤回，释放 GUI 引用。"""
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        if self._recall:
            try:
                self._recall.close()
            except Exception:
                pass
            self._recall = None
        self._gui = None
        self._wx = None

    def _data_dir(self) -> str:
        if self._cfg.data_dir:
            os.makedirs(self._cfg.data_dir, exist_ok=True)
            return self._cfg.data_dir
        path = os.path.join(os.path.expanduser("~"), ".wcauto_bridge")
        os.makedirs(path, exist_ok=True)
        return path

    # ------------------------------------------------------------------
    # 入站消息（Listener 工作线程内执行）
    # ------------------------------------------------------------------

    def _on_message(self, msg: Dict[str, Any], listener: Any) -> None:
        """Listener 回调：过滤/富化后向频道推送 message 事件。"""
        try:
            username = str(msg.get("username") or "")
            if not username or username in self._cfg.ignore_chats:
                return
            try:
                from wechatauto.recall import _is_revoke_row
                if _is_revoke_row(msg):
                    return  # 撤回行由 RecallGuard 走 recall 事件
            except Exception:
                pass
            chat_name = self._nickname(username)
            data = {
                "username": username,
                "chat_name": chat_name,
                "is_group": username.endswith("@chatroom"),
                "local_id": msg.get("local_id"),
                "shard": msg.get("shard") or "",
                "type": str(msg.get("type") or ""),
                "sender_id": msg.get("sender_id"),
                "sender_username": str(msg.get("sender_username") or ""),
                "sender_name": self._nickname(str(msg.get("sender_username") or "")),
                "content": str(msg.get("content") or ""),
                "create_time": msg.get("create_time") or int(time.time()),
                "sort_seq": msg.get("sort_seq") or 0,
            }
            if data["chat_name"] and data["chat_name"] in self._cfg.ignore_chats:
                return
            self._emit(protocol.EVENT_MESSAGE, data)
        except Exception:
            pass

    def _nickname(self, username: str) -> str:
        """wxid → 显示名（备注/昵称），带 1h 缓存；失败回退原名。"""
        if not username:
            return ""
        now = time.time()
        hit = self._nick_cache.get(username)
        if hit and now - hit[1] < 3600:
            return hit[0]
        name = username
        try:
            if self._db is not None:
                name = self._db.get_nickname(username) or username
        except Exception:
            pass
        self._nick_cache[username] = (name, now)
        if len(self._nick_cache) > 5000:
            self._nick_cache.clear()
        return name

    # ------------------------------------------------------------------
    # GUI 写侧（惰性）
    # ------------------------------------------------------------------

    def _get_gui(self) -> Any:
        """取 GUI 驱动；窗口不存在/不可见时抛 BridgeError。"""
        with self._gui_lock:
            if self._gui is not None:
                return self._gui
            try:
                from wechatauto.guia import WeChatGUI
                self._gui = WeChatGUI()
                self._gui_err = ""
            except Exception as exc:
                self._gui_err = str(exc)[:300]
                raise BridgeError(
                    f"微信窗口不可用（须已登录且窗口可见，锁屏/远程桌面断开时发送会失败）: {exc}"
                ) from exc
            return self._gui

    def _get_wx(self) -> Any:
        """取高层 WeChat 对象（朋友圈互动/标签群发/转发用）。"""
        if self._wx is None:
            from wechatauto.wx import WeChat
            self._wx = WeChat(gui=self._get_gui(), db=self._db)
        return self._wx

    def _display_name(self, chat: str) -> str:
        """发送目标归一：wxid/@chatroom → 界面显示名（搜索框只认显示名）。"""
        chat = str(chat or "").strip()
        if not chat:
            raise BridgeError("发送目标为空")
        if chat.startswith("wxid_") or chat.endswith("@chatroom"):
            return self._nickname(chat)
        return chat

    # ------------------------------------------------------------------
    # RPC 分发
    # ------------------------------------------------------------------

    def handle_rpc(self, method: str, params: Dict[str, Any]) -> Any:
        """RPC 统一入口；BridgeError 与未知异常都由 server 转成错误响应。"""
        handler = _RPC_TABLE.get(method)
        if handler is None:
            raise BridgeError(f"未知 RPC 方法: {method}")
        return handler(self, **(params or {}))

    # -- 状态/信息（读，AUTO_ALLOW 语义）--------------------------------

    def ping(self) -> Dict[str, Any]:
        """轻量探活：桥进程状态 + 监听/窗口可用性。"""
        window_visible = False
        if self._gui is not None:
            try:
                window_visible = bool(self._gui.ensure_visible())
            except Exception:
                window_visible = False
        return {
            "pong": True,
            "uptime_sec": int(time.time() - self._started_at),
            "account": self._self_info.get("username", ""),
            "nickname": self._self_info.get("nick_name", ""),
            "listener_running": bool(
                self._listener and self._listener._thread
                and self._listener._thread.is_alive()),
            "gui_ready": self._gui is not None,
            "gui_error": self._gui_err,
            "window_visible": window_visible,
            "recall_guard": self._recall is not None,
        }

    def doctor(self) -> Dict[str, Any]:
        """深度自检：账号/数据库密钥/图片密钥/控件树四项。"""
        result: Dict[str, Any] = {"account": None, "db_ok": False,
                                  "image_key": False, "window_visible": False}
        try:
            result["account"] = self._self_info or dict(self._db.get_self_info() or {})
            result["db_ok"] = bool(result["account"].get("username"))
        except Exception as exc:
            result["db_error"] = str(exc)[:300]
        try:
            result["image_key"] = bool(self._media.detect_image_key())
        except Exception:
            pass
        try:
            result["window_visible"] = bool(self._get_gui().ensure_visible())
        except BridgeError as exc:
            result["window_error"] = str(exc)[:300]
        result["listener"] = self.ping()
        return result

    def get_self_info(self) -> Dict[str, Any]:
        if not self._self_info and self._db is not None:
            self._self_info = dict(self._db.get_self_info() or {})
        return dict(self._self_info)

    def get_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._db.get_sessions(limit=min(max(int(limit), 1), 500))
        for row in rows:
            row["chat_name"] = self._nickname(str(row.get("username") or ""))
            row["is_group"] = str(row.get("username") or "").endswith("@chatroom")
        return rows

    def get_messages(self, chat: str, limit: int = 20) -> List[Dict[str, Any]]:
        return self._db.get_messages(str(chat), limit=min(max(int(limit), 1), 200))

    def search_contact(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        hits = self._db.search_contact(str(keyword))
        return list(hits or [])[: max(int(limit), 1)]

    def get_group_members(self, group: str) -> List[Dict[str, Any]]:
        return self._db.get_group_members(str(group))

    def list_labels(self) -> Any:
        return self._db.list_labels()

    def label_members(self, label: str) -> Any:
        ops = self._label_ops()
        return ops.label_members(str(label)) if ops else self._err_no_uia()

    # -- 媒体下载（读）---------------------------------------------------

    def download_media(self, chat: str, local_id: int) -> Dict[str, Any]:
        """下载一条消息的媒体并 base64 回传（受 media_max_mb 上限）。"""
        if self._media is None:
            raise BridgeError("媒体下载器未初始化（桥未启动）")
        path = self._media.download_media(
            str(chat), int(local_id), save_dir=self._downloads_dir)
        if not path or not os.path.isfile(path):
            raise BridgeError(
                f"媒体不可用（未落盘或微信未下载该附件）: chat={chat} local_id={local_id}")
        size = os.path.getsize(path)
        if size > self._cfg.media_max_bytes:
            raise BridgeError(
                f"媒体过大（{size / 1024 / 1024:.1f}MB > {self._cfg.media_max_mb}MB 上限）")
        with open(path, "rb") as f:
            data_b64 = base64.b64encode(f.read()).decode()
        return {
            "path": path,
            "filename": os.path.basename(path),
            "size": size,
            "data_b64": data_b64,
        }

    # -- 发送（写，GUI；全部过 _gui_lock 串行 + wechatauto rhythm 节流）---

    def send_msg(self, chat: str, text: str, verify: bool = True) -> Dict[str, Any]:
        if not str(text).strip():
            raise BridgeError("发送内容为空")
        with self._gui_lock:
            resp = self._get_gui().send_msg(str(text), who=self._display_name(chat),
                                            verify=bool(verify))
        return dict(resp)

    def send_at(self, chat: str, member: str, text: str,
                verify: bool = True) -> Dict[str, Any]:
        with self._gui_lock:
            resp = self._get_gui().at_member(
                str(member), str(text), who=self._display_name(chat), verify=bool(verify))
        return dict(resp)

    def _send_file_like(self, chat: str, filename: str, data_b64: str,
                        verify: bool, image: bool) -> Dict[str, Any]:
        raw = base64.b64decode(data_b64 or "")
        if not raw:
            raise BridgeError("文件内容为空")
        if len(raw) > self._cfg.media_max_bytes * 4:
            raise BridgeError("出站文件超过桥上限（100MB）")
        outbox = os.path.join(self._data_dir(), "outbox")
        os.makedirs(outbox, exist_ok=True)
        safe_name = os.path.basename(filename or "file.bin") or "file.bin"
        path = os.path.join(outbox, f"{int(time.time() * 1000)}_{safe_name}")
        with open(path, "wb") as f:
            f.write(raw)
        try:
            with self._gui_lock:
                gui = self._get_gui()
                if image:
                    resp = gui.send_image(path, who=self._display_name(chat),
                                          verify=bool(verify))
                else:
                    resp = gui.send_file(path, who=self._display_name(chat),
                                         verify=bool(verify))
            return dict(resp)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def send_file(self, chat: str, filename: str, data_b64: str,
                  verify: bool = True) -> Dict[str, Any]:
        return self._send_file_like(chat, filename, data_b64, verify, image=False)

    def send_image(self, chat: str, filename: str, data_b64: str,
                   verify: bool = True) -> Dict[str, Any]:
        return self._send_file_like(chat, filename, data_b64, verify, image=True)

    # -- 消息操作（写）----------------------------------------------------

    def recall_last(self, chat: str) -> Dict[str, Any]:
        with self._gui_lock:
            from wechatauto.wx import Chat
            chat_obj = Chat(self._display_name(chat), self._get_gui(), self._db)
            resp = chat_obj.RecallLastMessage()
        return dict(resp)

    def get_recalled(self, chat: str = "", limit: int = 20) -> Any:
        if self._recall is None:
            raise BridgeError("防撤回未启用（enable_recall_guard=false）")
        return self._recall.get_recalled(chat or None, min(max(int(limit), 1), 100))

    def forward_message(self, chat: str, targets: List[str],
                        match: str = "") -> Dict[str, Any]:
        targets = [str(t).strip() for t in (targets or []) if str(t).strip()]
        if not targets:
            raise BridgeError("转发目标为空")
        with self._gui_lock:
            ops = self._forward_ops()
            return ops.forward(targets, chat=self._display_name(chat),
                               match=match or None)

    # -- 朋友圈 ------------------------------------------------------------

    def get_moments(self, limit: int = 20, offset: int = 0,
                    username: str = "", keyword: str = "",
                    since: int = 0) -> List[Dict[str, Any]]:
        if not self._cfg.enable_moments:
            raise BridgeError("朋友圈读取未启用（enable_moments=false）")
        from wechatauto.moment import MomentDB
        mdb = MomentDB(self._db)
        return mdb.get_moments(
            limit=min(max(int(limit), 1), 100),
            offset=max(int(offset), 0),
            username=username or None,
            keyword=keyword or None,
            since=int(since) or None,
        )

    def moment_like(self, publisher: str = "", keyword: str = "") -> Dict[str, Any]:
        with self._gui_lock:
            from wechatauto.moment import Moment, MomentDB
            moment = Moment(self._get_wx())
            resp = moment.LikeMoment(publisher=publisher or None,
                                     keyword=keyword or None,
                                     db=MomentDB(self._db))
        return dict(resp)

    def moment_comment(self, content: str, publisher: str = "",
                       keyword: str = "") -> Dict[str, Any]:
        if not content.strip():
            raise BridgeError("评论内容为空")
        with self._gui_lock:
            from wechatauto.moment import Moment, MomentDB
            moment = Moment(self._get_wx())
            resp = moment.CommentMoment(publisher=publisher or None,
                                        keyword=keyword or None,
                                        content=content,
                                        db=MomentDB(self._db))
        return dict(resp)

    # -- 标签群发（写）-----------------------------------------------------

    def label_send(self, label: str, text: str,
                   dry_run: bool = True) -> Dict[str, Any]:
        """按标签群发；dry_run=True 只解析收件人不发送（首用必须先跑 dry_run）。"""
        with self._gui_lock:
            resp = self._get_wx().SendToLabel(str(text), str(label),
                                              dry_run=bool(dry_run))
        return dict(resp)

    # -- UIA 引擎 ---------------------------------------------------------

    def _label_ops(self) -> Any:
        try:
            from wechatauto.labels import LabelOps
            eng = self._get_gui()._get_uia()
            if eng is None:
                eng = self._get_gui()._get_uia(refresh=True)
            if eng is None:
                return None
            return LabelOps(uia=eng, db=self._db)
        except BridgeError:
            return None

    def _forward_ops(self) -> Any:
        from wechatauto.forward import ForwardOps
        eng = self._get_gui()._get_uia()
        if eng is None:
            eng = self._get_gui()._get_uia(refresh=True)
        if eng is None:
            raise BridgeError("UIA 控件树不可用（微信窗口须可见且未最小化到托盘）")
        return ForwardOps(uia=eng, db=self._db)

    def _err_no_uia(self) -> Dict[str, Any]:
        return {"ok": False, "reason": "UIA 控件树不可用", "members": []}


# RPC 方法 → 后端方法（协议方法名白名单，未列出的一律拒绝）
_RPC_TABLE: Dict[str, Callable[..., Any]] = {
    protocol.RPC_PING: WeChatBackend.ping,
    protocol.RPC_DOCTOR: WeChatBackend.doctor,
    protocol.RPC_GET_SELF_INFO: WeChatBackend.get_self_info,
    protocol.RPC_GET_SESSIONS: WeChatBackend.get_sessions,
    protocol.RPC_GET_MESSAGES: WeChatBackend.get_messages,
    protocol.RPC_SEARCH_CONTACT: WeChatBackend.search_contact,
    protocol.RPC_GET_GROUP_MEMBERS: WeChatBackend.get_group_members,
    protocol.RPC_LIST_LABELS: WeChatBackend.list_labels,
    protocol.RPC_LABEL_MEMBERS: WeChatBackend.label_members,
    protocol.RPC_DOWNLOAD_MEDIA: WeChatBackend.download_media,
    protocol.RPC_SEND_MSG: WeChatBackend.send_msg,
    protocol.RPC_SEND_AT: WeChatBackend.send_at,
    protocol.RPC_SEND_FILE: WeChatBackend.send_file,
    protocol.RPC_SEND_IMAGE: WeChatBackend.send_image,
    protocol.RPC_RECALL_LAST: WeChatBackend.recall_last,
    protocol.RPC_GET_RECALLED: WeChatBackend.get_recalled,
    protocol.RPC_GET_MOMENTS: WeChatBackend.get_moments,
    protocol.RPC_MOMENT_LIKE: WeChatBackend.moment_like,
    protocol.RPC_MOMENT_COMMENT: WeChatBackend.moment_comment,
    protocol.RPC_FORWARD: WeChatBackend.forward_message,
    protocol.RPC_LABEL_SEND: WeChatBackend.label_send,
}
