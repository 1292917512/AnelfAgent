"""AcFun 客户端 — acfunsdk.Acer（同步 httpx）的异步封装与能力门面。

acfunsdk 是同步阻塞客户端：所有网络调用经 ``asyncio.to_thread`` 移出事件循环，
并以一把 asyncio.Lock 串行化（cookie jar 全实例共享，防并发写竞态）。

SDK 0.9.8 已知缺陷集中在此绕行（绕行点均为 SDK 自身的 AcSource 常量 + 轻量直连，
不引入第二条 HTTP 通路）：

- ``AcSearch.page`` / ``AcUp.video`` 等对象构造路径参数错位（多传一个 dict 直接 TypeError），
  搜索与空间列表改为复用 SDK 的 ajax 取数 + 本地解析 HTML 载荷；
- ``AcDanmaku.__init__`` 一次性拉全量弹幕无分页，改为直连单页接口；
- ``AcComment.__init__`` 为取 referer 额外抓整页，评论写操作改为直连接口（1 次请求）。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar

from acfunsdk.exceptions import AcExploded, NotInCar, ShuiNi, TingBuDong
from acfunsdk.source import AcSource

from core.log import log

T = TypeVar("T")

# 评论接口 sourceType 映射（对齐 acfunsdk.page.comment.AcComment.resource_type_map）
_COMMENT_SOURCE_TYPE = {"1": 2, "2": 3, "3": 1, "10": 4}
# rtype → 内容页路由键（构造 referer / 解析 URL 共用）
_RTYPE_ROUTE_KEY = {"1": "bangumi", "2": "video", "3": "article", "10": "moment"}


class AcfunClient:
    """AcFun 会话客户端：登录态管理 + 全量 API 的同步方法集合（经 ``run`` 异步化）。"""

    def __init__(self) -> None:
        self._acer: Any = None  # acfunsdk.Acer（无类型存根，按 Any 持有）
        self._lock = asyncio.Lock()
        self._msg_req_count = 0  # 通知 ajax 的 reqID 计数

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------

    async def run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """串行化地把同步 SDK 调用移出事件循环执行。"""
        async with self._lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    @property
    def is_logined(self) -> bool:
        return bool(self._acer is not None and self._acer.is_logined)

    @property
    def uid(self) -> str:
        return str(self._acer.uid) if self.is_logined else ""

    @property
    def username(self) -> str:
        return str(self._acer.username or "") if self.is_logined else ""

    @property
    def profile(self) -> Dict[str, Any]:
        return dict(self._acer.data) if self.is_logined else {}

    def require_acer(self) -> Any:
        """取已登录的 Acer 实例，未登录抛 NotInCar。"""
        if not self.is_logined:
            raise NotInCar("AcFun 未登录，请先在频道页完成账号登录")
        return self._acer

    @staticmethod
    def _new_acer() -> Any:
        """创建 Acer 实例（构造即访问网络：获取 _did 与访客/登录令牌）。

        acfunsdk 默认 httpx 5s 超时对 AcFun 的服务端渲染页（通知中心/直播
        接口）过紧，实测通知页经常超过 5s——放宽为读 30s / 连接 10s。
        """
        import httpx
        from acfunsdk import Acer

        acer = Acer()
        acer.client.timeout = httpx.Timeout(30.0, connect=10.0)
        return acer

    @staticmethod
    def _close_acer(acer: Any) -> None:
        try:
            acer.client.close()
        except Exception:
            pass

    def close(self) -> None:
        """关闭底层 httpx 客户端（不影响服务端会话，stop 时调用）。"""
        if self._acer is not None:
            self._close_acer(self._acer)
            self._acer = None

    # ------------------------------------------------------------------
    # 登录 / 会话
    # ------------------------------------------------------------------

    def login_sync(self, username: str, password: str, key: str = "", captcha: str = "") -> Dict[str, Any]:
        """账号密码登录（同步，经 run 调用）。成功返回含 cookies 的结果供持久化。"""
        acer = self._new_acer()
        try:
            req = acer.client.post(AcSource.apis["login"], data={
                "username": username,
                "password": password,
                "key": key or "",
                "captcha": captcha or "",
            })
            result = req.json()
        except Exception as exc:
            self._close_acer(acer)
            raise AcExploded(f"登录请求失败: {exc}") from exc
        if result.get("result") != 0:
            self._close_acer(acer)
            return {
                "success": False,
                "error_msg": result.get("error_msg") or f"登录失败 (result={result.get('result')})",
                "need_captcha": bool(result.get("needCaptcha") or result.get("captcha")),
            }
        acer.is_logined = True
        acer._get_personal()  # 校验会话 + 填充资料（含自动签到）
        old, self._acer = self._acer, acer
        if old is not None:
            self._close_acer(old)
        return {
            "success": True,
            "uid": acer.uid,
            "username": acer.username,
            "cookies": dict(acer.client.cookies.items()),
        }

    def restore_sync(self, cookies: Dict[str, str]) -> bool:
        """用持久化 cookie 恢复会话（同步）。cookie 失效返回 False。"""
        acer = self._new_acer()
        try:
            acer.client.cookies.update(cookies)
            acer.is_logined = True
            acer._get_personal()
        except Exception as exc:
            log(f"AcFun: cookie 会话恢复失败: {exc}", "DEBUG", tag="通道")
            self._close_acer(acer)
            return False
        if not acer.data.get("userId"):
            self._close_acer(acer)
            return False
        old, self._acer = self._acer, acer
        if old is not None:
            self._close_acer(old)
        return True

    def logout_sync(self) -> None:
        """服务端登出并丢弃本地会话。"""
        if self._acer is not None:
            try:
                self._acer.logout()
            except Exception as exc:
                log(f"AcFun: 服务端登出异常已忽略: {exc}", "DEBUG", tag="通道")
            self._close_acer(self._acer)
            self._acer = None

    # ------------------------------------------------------------------
    # 通知中心（轮询入站的数据源）
    # ------------------------------------------------------------------

    # SDK 的 message.py 刮削对空分类页/局部结构缺失会整页抛 None.attrs，
    # 且单条坏条目拖垮整页——通知解析改为自研容错实现：ajax 取数 +
    # 逐项隔离解析（坏条目跳过，整页永不抛）。acfunsdk 仅复用其 apis 常量。

    _NOTIFY_VID = {"reply": "", "like": "like", "at": "atmine",
                   "gift": "gift", "notice": "sysmsg", "system": "resmsg"}

    def unread(self) -> Optional[Dict[str, int]]:
        """未读计数 {new_comment/at_notify/...}；异常或未登录返回 None。"""
        acer = self.require_acer()
        if acer.message is None:
            return None
        try:
            return acer.message.unread
        except Exception:
            return None

    def get_notifications(self, kind: str, page: int = 1) -> List[Dict[str, Any]]:
        """拉取一页通知（逐项容错解析；空页/缺尾标按空列表，坏条目跳过）。"""
        import json as _json
        import re as _re

        from acfunsdk.page.utils import Bs

        acer = self.require_acer()
        vid = self._NOTIFY_VID.get(kind)
        if vid is None:
            return []
        self._msg_req_count += 1
        req = acer.client.get(AcSource.apis["message"] + vid, params={
            "quickViewId": "upCollageMain",
            "reqID": self._msg_req_count,
            "ajaxpipe": 1,
            "pageNum": page,
            "t": str(time.time_ns())[:13],
        })
        if not req.text.endswith("/*<!-- fetch-stream -->*/"):
            return []  # 无尾标 = 空页/未授权页
        try:
            page_obj = Bs(_json.loads(req.text[:-25]).get("html", ""), "lxml")
        except Exception as exc:
            log(f"AcFun: 通知页 HTML 载荷解析失败 kind={kind}: {exc}", "DEBUG", tag="通道")
            return []
        if page_obj.select_one("#listview") is None:
            return []  # 真正的空分类页
        items: List[Dict[str, Any]] = []
        skipped = 0
        for item in page_obj.select("#listview > ul, .main-block-msg-item"):
            try:
                parsed = self._parse_notify_item(vid, item, _re)
            except Exception:
                parsed = None
            if parsed is None:
                skipped += 1
            else:
                items.append(parsed)
        if skipped:
            log(f"AcFun: 通知页 {skipped} 条坏条目已跳过 kind={kind}", "DEBUG", tag="通道")
        return items

    @staticmethod
    def _notify_txt(node: Any, selector: str) -> str:
        el = node.select_one(selector)
        return el.text.strip().replace("\xa0", " ") if el is not None else ""

    @staticmethod
    def _notify_href(node: Any, selector: str) -> str:
        el = node.select_one(selector)
        if el is None:
            return ""
        return str(el.attrs.get("href", ""))

    @staticmethod
    def _notify_uid(href: str, re_mod: Any) -> str:
        match = re_mod.search(r"/u/(\d+)", href)
        return match.group(1) if match else ""

    @staticmethod
    def _notify_ncid(url: str) -> str:
        return url.split("#ncid=", 1)[1] if "#ncid=" in url else ""

    def _parse_notify_item(self, vid: str, item: Any, re_mod: Any) -> Optional[Dict[str, Any]]:
        """按类别解析单条通知（容错版；关键字段全缺失返回 None 跳过）。"""
        if vid == "":  # reply 评论回复
            content = self._notify_txt(item, ".msg-reply .inner")
            if not content:
                return None
            return {
                "content_url": self._abs_url(self._notify_href(item, ".intro a")),
                "content_title": self._notify_txt(item, ".intro a"),
                "replied": self._notify_txt(item, ".msg-replied .inner"),
                "uid": self._notify_uid(self._notify_href(item, ".titlebar .name"), re_mod),
                "username": self._notify_txt(item, ".titlebar .name"),
                "create_at": self._notify_txt(item, ".titlebar .time"),
                "ncid": self._notify_ncid(self._notify_href(item, "a.msg-reply")),
                "content": content,
                "intro": self._notify_txt(item, ".content .intro"),
            }
        if vid == "like":
            liked_url = self._notify_href(item, "a.replied")
            main_url, _, ncid_part = liked_url.partition("#")
            return {
                "content_url": self._abs_url(main_url),
                "replied": self._notify_txt(item, ".clamp-text .inner"),
                "uid": self._notify_uid(self._notify_href(item, ".titlebar .name"), re_mod),
                "username": self._notify_txt(item, ".titlebar .name"),
                "ncid": ncid_part[5:] if ncid_part.startswith("ncid=") else "",
                "create_at": self._notify_txt(item, ".titlebar span.time"),
                "intro": self._notify_txt(item, ".titlebar"),
            }
        if vid == "atmine":
            at_url = self._notify_href(item, ".content .msg-text") or self._notify_href(item, ".content a")
            return {
                "content_url": self._abs_url(at_url.split("#", 1)[0]),
                "ncid": self._notify_ncid(at_url),
                "uid": self._notify_uid(self._notify_href(item, ".avatar-section"), re_mod),
                "username": self._notify_txt(item, ".titlebar-container .name"),
                "create_at": self._notify_txt(item, ".titlebar-container span.time"),
                "intro": self._notify_txt(item, ".titlebar-container .intro"),
            }
        if vid == "gift":
            classes = item.attrs.get("class", [])
            if "moment-gift" in classes:
                intro = self._notify_txt(item, ".msg-content")
                return {
                    "classify": "moment",
                    "content_url": self._abs_url(self._notify_href(item, ".msg-content a")),
                    "content_title": "动态",
                    "uid": self._notify_uid(self._notify_href(item, ".avatar-section"), re_mod),
                    "username": self._notify_txt(item, ".content .name"),
                    "create_at": self._notify_txt(item, ".content span.time"),
                    "intro": intro,
                    "banana": self._notify_banana(intro, re_mod),
                }
            intro = self._notify_txt(item, "p")
            return {
                "classify": "content",
                "content_url": self._abs_url(self._notify_href(item, "p a:nth-of-type(2)")),
                "content_title": self._notify_txt(item, "p a:nth-of-type(2)"),
                "uid": self._notify_uid(self._notify_href(item, "p a:nth-of-type(1)"), re_mod),
                "username": self._notify_txt(item, "p a:nth-of-type(1)"),
                "create_at": self._notify_txt(item, ".msg-item-time"),
                "intro": intro,
                "banana": self._notify_banana(intro, re_mod),
            }
        if vid == "sysmsg":
            title = self._notify_txt(item, "div:nth-of-type(1)")
            content = self._notify_txt(item, "div:nth-of-type(2)")
            if not title and not content:
                return None
            return {
                "content_url": self._notify_href(item, "div:nth-of-type(2) a"),
                "content_title": title,
                "create_at": self._notify_txt(item, ".msg-item-time"),
                "intro": content,
            }
        # resmsg 系统通知（关注/过审/收藏等）
        intro = self._notify_txt(item, "p:nth-of-type(1)")
        if not intro:
            return None
        links: Dict[str, List[str]] = {}
        for link in item.select("a"):
            url = self._abs_url(str(link.attrs.get("href", "")))
            for link_name in ("video", "article", "album", "bangumi", "up", "live"):
                if url.startswith(AcSource.routes[link_name]) and link_name not in links:
                    links[link_name] = [link.text.strip(), url]
        return {"create_at": self._notify_txt(item, "p.msg-item-time"), "intro": intro, **links}

    @staticmethod
    def _abs_url(url: str) -> str:
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return f"{AcSource.routes['index']}{url}"
        return url

    @staticmethod
    def _notify_banana(intro: str, re_mod: Any) -> int:
        match = re_mod.search(r"(\d+)\s*根香蕉", intro)
        return int(match.group(1)) if match else 0

    # ------------------------------------------------------------------
    # 评论（rtype: 1 番剧 / 2 视频 / 3 文章 / 10 动态）
    # ------------------------------------------------------------------

    @staticmethod
    def _referer(rtype: Any, rid: Any) -> str:
        route_key = _RTYPE_ROUTE_KEY.get(str(rtype))
        if route_key is None:
            raise ValueError(f"不支持的内容类型 rtype={rtype!r}（1 番剧/2 视频/3 文章/10 动态）")
        return f"{AcSource.routes[route_key]}{rid}"

    @staticmethod
    def _source_type(rtype: Any) -> int:
        source_type = _COMMENT_SOURCE_TYPE.get(str(rtype))
        if source_type is None:
            raise ValueError(f"内容类型 rtype={rtype!r} 不支持评论（仅 1/2/3/10）")
        return source_type

    def send_comment(self, rtype: Any, rid: Any, content: str, reply_id: Any = None) -> bool:
        """发表评论 / 回复评论（直连 comment_add，免去整页抓取）。"""
        acer = self.require_acer()
        req = acer.client.post(AcSource.apis["comment_add"], data={
            "sourceId": str(rid),
            "sourceType": self._source_type(rtype),
            "content": content,
            "replyToCommentId": str(reply_id or ""),
        }, headers={"referer": self._referer(rtype, rid)})
        return req.json().get("result") == 0

    def list_comments(self, rtype: Any, rid: Any, page: int = 1) -> Dict[str, Any]:
        """拉取一页评论（root + 楼中楼预览），输出精简字段。"""
        acer = self.require_acer()
        req = acer.client.get(AcSource.apis["comment"], params={
            "sourceId": str(rid),
            "sourceType": self._source_type(rtype),
            "page": page,
            "pivotCommentId": 0,
            "newPivotCommentId": "",
            "t": str(time.time_ns())[:13],
            "supportZtEmot": True,
        })
        data = req.json()
        if data.get("result") != 0:
            raise TingBuDong(f"comment list result={data.get('result')!r}")
        comments = [self._trim_comment(c) for c in data.get("rootComments", [])]
        subs = {
            str(root_id): [self._trim_comment(c) for c in sub.get("subComments", [])]
            for root_id, sub in (data.get("subCommentsMap") or {}).items()
        }
        return {
            "comments": comments,
            "sub_comments": subs,
            "page": data.get("curPage", page),
            "total_page": data.get("totalPage", 1),
            "comment_count": data.get("commentCount", 0),
        }

    @staticmethod
    def _trim_comment(c: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "comment_id": c.get("commentId"),
            "floor": c.get("floor"),
            "user_id": c.get("userId"),
            "user_name": c.get("userName"),
            "content": c.get("content"),
            "like_count": c.get("likeCount"),
            "reply_count": c.get("replyCount"),
            "is_liked": c.get("isLiked"),
            "timestamp": c.get("timestamp"),
        }

    def delete_comment(self, rtype: Any, rid: Any, comment_id: Any) -> bool:
        """删除自己的评论（直连 comment_delete）。"""
        acer = self.require_acer()
        req = acer.client.post(AcSource.apis["comment_delete"], data={
            "sourceId": str(rid),
            "sourceType": self._source_type(rtype),
            "commentId": str(comment_id),
        }, headers={"referer": self._referer(rtype, rid)})
        return req.json().get("result") == 0

    def like_comment(self, rtype: Any, rid: Any, comment_id: Any, on_off: bool = True) -> bool:
        """点赞 / 取消点赞评论。"""
        acer = self.require_acer()
        api = AcSource.apis["comment_like" if on_off else "comment_unlike"]
        req = acer.client.post(api, data={
            "sourceId": str(rid),
            "sourceType": self._source_type(rtype),
            "commentId": str(comment_id),
        }, headers={"referer": self._referer(rtype, rid)})
        return req.json().get("result") == 0

    # ------------------------------------------------------------------
    # 互动（点赞 / 投蕉 / 收藏 / 关注 / 签到 / 签名）
    # ------------------------------------------------------------------

    def like(self, rtype: Any, rid: Any, on_off: bool = True) -> bool:
        acer = self.require_acer()
        return acer.like_add(rtype, rid) if on_off else acer.like_delete(rtype, rid)

    def throw_banana(self, rtype: Any, rid: Any, count: int = 1) -> bool:
        acer = self.require_acer()
        return acer.throw_banana(rtype, rid, count)

    def favorite(self, rtype: Any, rid: Any, on_off: bool = True) -> bool:
        acer = self.require_acer()
        if acer.favourite is None:
            raise NotInCar()
        return acer.favourite.add(rtype, str(rid)) if on_off else acer.favourite.cancel(rtype, str(rid))

    def follow(self, uid: Any, special: bool = False) -> bool:
        """关注用户；special=True 设为特别关注。"""
        acer = self.require_acer()
        if acer.follow is None:
            raise NotInCar()
        return acer.follow.add(uid, True if special else None)

    def unfollow(self, uid: Any) -> bool:
        acer = self.require_acer()
        if acer.follow is None:
            raise NotInCar()
        return acer.follow.remove(uid)

    def signin(self) -> bool:
        return bool(self.require_acer().signin())

    def update_signature(self, text: str) -> bool:
        return bool(self.require_acer().setup_signature(text))

    def acoin(self) -> Optional[Dict[str, Any]]:
        return self.require_acer().acoin()

    # ------------------------------------------------------------------
    # 搜索 / 内容信息 / 用户空间
    # ------------------------------------------------------------------

    def search(self, keyword: str, search_type: str = "complex", page: int = 1, sortby: int = 1) -> List[Dict[str, Any]]:
        """全站搜索。SDK 对象构造路径有缺陷，此处复用其 ajax 取数并本地解析结果项。"""
        from acfunsdk.page.search import AcSearch

        acer = self.require_acer()
        search = AcSearch(acer, keyword, search_type if search_type in AcSearch.search_types else "complex")
        search._get_data(page, sortby if sortby in range(1, 6) else 1)
        items: List[Dict[str, Any]] = []
        for item in search.result_obj.select("[class^=search-]"):
            classes = [str(c) for c in item.attrs.get("class", [])]
            kind = next((c[len("search-"):] for c in classes if c.startswith("search-")), "unknown")
            if "data-up-exposure-log" in item.attrs:
                data = json.loads(item.attrs["data-up-exposure-log"])
                items.append({"type": "user", "uid": data.get("up_id"), "name": data.get("up_name", "")})
            elif "data-exposure-log" in item.attrs:
                data = json.loads(item.attrs["data-exposure-log"])
                items.append({"type": kind, "id": data.get("content_id"), "title": data.get("title", "")})
        return items

    def video_info(self, ac_id: Any) -> Dict[str, Any]:
        from acfunsdk.page.video import AcVideo

        video = AcVideo(self.require_acer(), ac_id)
        if video.is_404:
            raise ShuiNi(f"视频不存在: ac{ac_id}")
        data = video.raw_data
        return {
            "ac_id": f"ac{video.resource_id}",
            "title": video.title,
            "up_uid": data.get("user", {}).get("id"),
            "up_name": data.get("user", {}).get("name"),
            "description": data.get("description", ""),
            "cover": video.cover,
            "view_count": video.view_count,
            "like_count": video.like_count,
            "comment_count": video.comment_count,
            "stow_count": video.stow_count,
            "banana_count": video.banana_count,
            "share_count": video.share_count,
            "create_time": str(video.create_time),
            "parts": len(video.video_list),
            "url": video.referer,
        }

    def article_info(self, ac_id: Any) -> Dict[str, Any]:
        from acfunsdk.page.article import AcArticle

        article = AcArticle(self.require_acer(), ac_id)
        if article.is_404:
            raise ShuiNi(f"文章不存在: ac{ac_id}")
        data = article.raw_data
        return {
            "ac_id": f"ac{article.resource_id}",
            "title": article.title,
            "up_uid": data.get("user", {}).get("id"),
            "up_name": data.get("user", {}).get("name"),
            "tags": article.tags,
            "cover": article.cover,
            "view_count": article.view_count,
            "like_count": article.like_count,
            "comment_count": article.comment_count,
            "stow_count": article.stow_count,
            "banana_count": article.banana_count,
            "create_time": str(article.create_time),
            "url": article.referer,
        }

    def bangumi_info(self, aa_id: Any) -> Dict[str, Any]:
        from acfunsdk.page.bangumi import AcBangumi

        bangumi = AcBangumi(self.require_acer(), aa_id)
        if bangumi.is_404:
            raise ShuiNi(f"番剧不存在: aa{aa_id}")
        data = bangumi.raw_data.get("data", {}) if isinstance(bangumi.raw_data, dict) else {}
        episodes = bangumi.raw_data.get("list", []) if isinstance(bangumi.raw_data, dict) else []
        return {
            "aa_id": f"aa{bangumi.resource_id}",
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "cover": data.get("coverImage") or data.get("coverUrl", ""),
            "episode_count": len(episodes),
            "url": bangumi.referer,
        }

    def user_info(self, uid: Any) -> Dict[str, Any]:
        acer = self.require_acer()
        up = acer.acfun.AcUp(uid)
        if up.is_404:
            raise ShuiNi(f"用户不存在: {uid}")
        profile = dict(up.raw_data)
        profile["uid"] = up.uid
        profile["url"] = up.referer
        return profile

    def user_content(self, uid: Any, kind: str = "video", page: int = 1,
                     limit: int = 10, orderby: str = "newest") -> List[Dict[str, Any]]:
        """用户空间列表（video/article/album/following/followed）。

        SDK 的对象构造路径有缺陷，此处复用其 ajax 取数并本地解析 HTML 载荷。
        """
        from acfunsdk.page.utils import Bs

        acer = self.require_acer()
        up = acer.acfun.AcUp(uid)
        if up.is_404:
            raise ShuiNi(f"用户不存在: {uid}")
        data = up._get_data(kind, page, limit, orderby)
        page_obj = Bs(data.get("html", ""), "lxml")
        items: List[Dict[str, Any]] = []
        if kind == "video":
            for item in page_obj.select("a.ac-space-video"):
                title_node = item.select_one("p.title")
                items.append({
                    "ac_id": item.attrs["href"][5:],
                    "title": title_node.attrs.get("title", "") if title_node else "",
                })
        elif kind == "article":
            for item in page_obj.select(".ac-space-article"):
                link = item.select_one("a")
                items.append({
                    "ac_id": link.attrs["href"][5:] if link else "",
                    "title": link.attrs.get("title", "") if link else "",
                })
        elif kind == "album":
            for item in page_obj.select(".ac-space-album"):
                link = item.select_one("a")
                items.append({
                    "aa_id": link.attrs["href"][5:] if link else "",
                    "title": link.attrs.get("title", "") if link else "",
                })
        else:  # following / followed
            for item in page_obj.select("li"):
                link = item.select_one("div:nth-of-type(2) > a.name")
                if link is not None:
                    items.append({"uid": link.attrs["href"][3:], "name": link.text})
        return items

    def moment_info(self, am_id: Any) -> Dict[str, Any]:
        acer = self.require_acer()
        moment = acer.acfun.AcMoment(am_id)
        if moment.is_404:
            raise ShuiNi(f"动态不存在: am{am_id}")
        data = moment.moment_data or {}
        return {
            "am_id": f"am{moment.resource_id}",
            "text": moment.title,
            "up_uid": data.get("user", {}).get("id"),
            "up_name": data.get("user", {}).get("name"),
            "images": len(data.get("imgs", [])),
            "view_count": moment.view_count,
            "like_count": moment.like_count,
            "comment_count": moment.comment_count,
            "url": moment.referer,
        }

    # ------------------------------------------------------------------
    # 动态流 / 历史 / 收藏夹 / 关注列表
    # ------------------------------------------------------------------

    def moment_feed(self, limit: int = 10, tab: str = "all", refresh: bool = False) -> List[Dict[str, Any]]:
        acer = self.require_acer()
        if acer.moment is None:
            raise NotInCar()
        acer.moment.set_tab(tab)
        feed = acer.moment.feed(limit=limit, obj=False, refresh=refresh) or []
        items: List[Dict[str, Any]] = []
        for item in feed:
            moment = item.get("moment") if isinstance(item.get("moment"), dict) else {}
            text = item.get("title") or moment.get("replaceUbbText", "")
            items.append({
                "resource_type": item.get("resourceType"),
                "resource_id": item.get("resourceId"),
                "up_name": (item.get("user") or {}).get("userName") or (item.get("user") or {}).get("name"),
                "title": str(text)[:80],
            })
        return items

    def history(self, page: int = 1, limit: int = 10) -> List[Dict[str, Any]]:
        acer = self.require_acer()
        data = acer.history(page=page, limit=limit, obj=False)
        return [{
            "resource_type": item.get("resourceType"),
            "resource_id": item.get("resourceId"),
            "title": item.get("title", ""),
            "up_name": (item.get("user") or {}).get("name", ""),
        } for item in data.get("histories", [])]

    def follow_groups(self) -> List[Dict[str, Any]]:
        acer = self.require_acer()
        if acer.follow is None:
            raise NotInCar()
        return acer.follow.groups() or []

    def my_fans(self, page: int = 1, limit: int = 10) -> List[Any]:
        acer = self.require_acer()
        if acer.follow is None:
            raise NotInCar()
        return acer.follow.my_fans(page=page, limit=limit) or []

    def favourite_folders(self) -> List[Dict[str, Any]]:
        acer = self.require_acer()
        if acer.favourite is None:
            raise NotInCar()
        return [{
            "folder_id": f.get("folderId"),
            "name": f.get("name"),
            "count": f.get("count"),
        } for f in acer.favourite.folders]

    def favourite_list(self, kind: str = "video", folder_id: Any = None,
                       page: int = 1, limit: int = 10) -> Optional[List[Any]]:
        """收藏夹内容。kind ∈ video/article/bangumi/album；video 需 folder_id（缺省用默认夹）。"""
        acer = self.require_acer()
        if acer.favourite is None:
            raise NotInCar()
        fav = acer.favourite
        if kind == "video":
            fid = folder_id or fav.default_fid
            if fid is None:
                return []
            return fav.video_list(fid, page=page, limit=limit)
        if kind == "article":
            return fav.article_list(page=page, limit=limit)
        if kind == "bangumi":
            return fav.bangumi_list(page=page, limit=limit)
        if kind == "album":
            return fav.album_list(page=page, limit=limit)
        raise ValueError(f"kind 必须是 video/article/bangumi/album: {kind!r}")

    # ------------------------------------------------------------------
    # 弹幕（VOD）
    # ------------------------------------------------------------------

    def _resolve_video_part(self, ac_id: Any, part: int = 0) -> Any:
        """解析稿件 + 分 P 的 video_id（弹幕接口用 videoId 而非 ac 号）。"""
        from acfunsdk.page.video import AcVideo

        video = AcVideo(self.require_acer(), ac_id)
        if video.is_404:
            raise ShuiNi(f"视频不存在: ac{ac_id}")
        if part not in range(len(video.video_list)):
            raise ValueError(f"分 P 索引越界: {part!r}，共 {len(video.video_list)}P")
        return video

    def video_danmaku_list(self, ac_id: Any, part: int = 0, page: int = 1) -> Dict[str, Any]:
        """拉取一页视频弹幕（直连单页接口；SDK 的 AcDanmaku 一次性拉全量不可用）。"""
        acer = self.require_acer()
        video = self._resolve_video_part(ac_id, part)
        vid = video.video_list[part]["id"]
        req = acer.client.get(AcSource.apis["danmaku"], params={
            "resourceId": str(vid),
            "resourceType": "9",
            "enableAdvanced": True,
            "pcursor": str(page),
            "count": "200",
            "sortType": "2",
            "asc": True,
        })
        data = req.json()
        danmakus = [{
            "danmaku_id": d.get("danmakuId"),
            "body": d.get("body"),
            "user_id": d.get("userId"),
            "position_ms": d.get("position"),
            "mode": d.get("mode"),
            "create_time": d.get("createTime"),
        } for d in data.get("danmakus", [])]
        return {"danmakus": danmakus, "total": data.get("totalCount", 0), "page": page, "video_id": vid}

    def video_danmaku_send(self, ac_id: Any, content: str, position_ms: int, part: int = 0,
                           color: int = 16777215, mode: int = 1, size: int = 25) -> bool:
        """发送视频弹幕。mode: 1 滚动 / 4 底部 / 5 顶部；size: 25 中 / 16 小。"""
        acer = self.require_acer()
        video = self._resolve_video_part(ac_id, part)
        vid = video.video_list[part]["id"]
        req = acer.client.post(AcSource.apis["danmaku_add"], data={
            "size": size,
            "mode": mode,
            "color": color,
            "position": position_ms,
            "body": content,
            "type": "douga",
            "videoId": vid,
            "id": video.resource_id,
            "subChannelId": video.raw_data.get("channel", {}).get("id"),
            "subChannelName": video.raw_data.get("channel", {}).get("name"),
            "roleId": "",
        }, headers={"referer": AcSource.routes["index"]})
        return req.json().get("result") == 0

    # ------------------------------------------------------------------
    # 直播
    # ------------------------------------------------------------------

    def live_list(self) -> List[Dict[str, Any]]:
        acer = self.require_acer()
        lives = acer.acfun.AcLive().list() or []
        return [{
            "uid": item.get("authorId"),
            "title": item.get("title"),
            "user_name": (item.get("user") or {}).get("name"),
            "cover": (item.get("coverUrls") or [None])[0],
            "category": item.get("categoryName", ""),
        } for item in lives]

    def _live_up(self, uid: Any) -> Any:
        return self.require_acer().acfun.AcLiveUp(uid)

    def live_ws_credentials(self) -> Optional[Dict[str, Any]]:
        """直播 websocket 建连凭据（klink 注册用；纯内存读）。"""
        if not self.is_logined:
            return None
        tokens = self._acer.tokens or {}
        ssecurity = tokens.get("ssecurity") or ""
        token = tokens.get("api_st") or tokens.get("visitor_st") or ""
        if not ssecurity or not token or not self._acer.did:
            return None
        return {
            "uid": int(self._acer.uid or 0),
            "did": str(self._acer.did),
            "ssecurity": str(ssecurity),
            "token": str(token),
        }

    def live_enter_params(self, uid: Any) -> Dict[str, Any]:
        """直播间进房参数（startPlay 接口）：liveId / 票列表 / enterRoomAttach。"""
        live_up = self._live_up(uid)
        live = getattr(live_up, "live", None)
        result: Dict[str, Any] = {
            "is_open": False,
            "title": live_up.title,
            "user_name": live_up.username,
        }
        if live is not None and getattr(live, "is_open", False):
            result.update({
                "is_open": True,
                "live_id": live.liveId,
                "tickets": list(live.availableTickets or []),
                "enter_room_attach": live.enterRoomAttach,
                "is_author": str(uid) == str(self.uid),
            })
        return result

    def live_info(self, uid: Any) -> Dict[str, Any]:
        live_up = self._live_up(uid)
        is_open = bool(live_up.live is not None and live_up.live.is_open)
        return {
            "uid": str(live_up.uid),
            "title": live_up.title,
            "user_name": live_up.username,
            "is_open": is_open,
            "start_time": live_up.live.start_time if is_open else "",
            "cover": live_up.cover,
            "url": live_up.referer,
        }

    def push_live_danmaku(self, uid: Any, content: str) -> bool:
        """向开播中的直播间发送弹幕（HTTP 通道）。"""
        live_up = self._live_up(uid)
        if live_up.live is None or live_up.live.is_open is False:
            raise ValueError(f"主播 {uid} 当前未开播")
        return bool(live_up.push_danmaku(content))

    def live_like(self, uid: Any, times: int = 1) -> bool:
        live_up = self._live_up(uid)
        if live_up.live is None or live_up.live.is_open is False:
            raise ValueError(f"主播 {uid} 当前未开播")
        return bool(live_up.like(min(max(int(times), 1), 10)))

    def live_gift_list(self, uid: Any) -> Dict[str, Any]:
        live_up = self._live_up(uid)
        if live_up.live is None or live_up.live.is_open is False:
            raise ValueError(f"主播 {uid} 当前未开播")
        gifts = live_up.gift_list() or {}
        return {
            str(gid): {
                "name": g.get("giftName"),
                "price": g.get("giftPrice"),
                "pay_type": {0: "free", 1: "acb", 2: "banana"}.get(g.get("payWalletType", 0), "unknown"),
                "batch_sizes": g.get("allowBatchSendSizeList", []),
            } for gid, g in gifts.items()
        }

    def live_send_gift(self, uid: Any, gift_id: Any, size: int = 1, times: int = 1) -> bool:
        live_up = self._live_up(uid)
        if live_up.live is None or live_up.live.is_open is False:
            raise ValueError(f"主播 {uid} 当前未开播")
        return bool(live_up.send_gift(int(gift_id), int(size), int(times)))

    def live_balance(self, uid: Any) -> Dict[str, Any]:
        """直播钱包余额（acb / 香蕉）。"""
        return self._live_up(uid).my_balance()

    # ------------------------------------------------------------------
    # 创作中心 / 粉丝团 / 合辑管理
    # ------------------------------------------------------------------

    def my_content(self, kind: str = "video", page: int = 1, status: str = "all",
                   sortby: str = "recently", keyword: str = "") -> List[Dict[str, Any]]:
        """我的稿件（创作中心）。kind ∈ video/article。"""
        acer = self.require_acer()
        if acer.contribute is None:
            raise NotInCar()
        if kind == "video":
            feed = acer.contribute.my_videos(page=page, status=status, sortby=sortby, keyword=keyword or None)
        elif kind == "article":
            feed = acer.contribute.my_articles(page=page, status=status, sortby=sortby, keyword=keyword or None)
        else:
            raise ValueError(f"kind 必须是 video/article: {kind!r}")
        return [{
            "ac_id": item.get("dougaId"),
            "title": item.get("title"),
            "create_time": item.get("createTime"),
            "view_count": item.get("viewCount"),
            "comment_count": item.get("commentCount"),
        } for item in (feed or [])]

    def data_center(self, days: int = 1) -> Optional[Dict[str, Any]]:
        acer = self.require_acer()
        if acer.contribute is None:
            raise NotInCar()
        return acer.contribute.data_center(days=days)

    def data_center_detail(self, rtype: str = "video", days: int = 1, sortby: str = "viewCount") -> Optional[Dict[str, Any]]:
        acer = self.require_acer()
        if acer.contribute is None:
            raise NotInCar()
        return acer.contribute.data_center_detail(rtype, days=days, sortby=sortby)

    def medal_list(self) -> List[Dict[str, Any]]:
        acer = self.require_acer()
        if acer.fansclub is None:
            raise NotInCar()
        return acer.fansclub.medal_list() or []

    def medal_wear(self, uid: Any, on_off: bool = True) -> Dict[str, Any]:
        acer = self.require_acer()
        if acer.fansclub is None:
            raise NotInCar()
        return acer.fansclub.medal_wear(uid, on_off)

    def album_list(self, page: int = 1, size: int = 10) -> Dict[str, Any]:
        acer = self.require_acer()
        if acer.album is None:
            raise NotInCar()
        return acer.album.list(page=page, size=size)

    def album_contents(self, album_id: Any, page: int = 1, size: int = 10) -> Dict[str, Any]:
        acer = self.require_acer()
        if acer.album is None:
            raise NotInCar()
        return acer.album.get_contents(album_id, page=page, size=size)

    def album_add(self, title: str, rtype: int, cover: str, intro: str, status: int = 1) -> Any:
        """创建合辑，返回合辑 ID。rtype: 2 视频 / 3 文章；status: 1 公开 / 2 私密。"""
        acer = self.require_acer()
        if acer.album is None:
            raise NotInCar()
        return acer.album.add(title, rtype, cover, intro, status)

    def album_remove(self, album_id: Any) -> bool:
        acer = self.require_acer()
        if acer.album is None:
            raise NotInCar()
        return bool(acer.album.remove(album_id))

    def album_update(self, album_id: Any, title: str, rtype: int, cover: str, intro: str, status: int = 1) -> bool:
        acer = self.require_acer()
        if acer.album is None:
            raise NotInCar()
        return bool(acer.album.update(album_id, title, rtype, cover, intro, status))

    def album_contents_add(self, album_id: Any, rtype: int, rids: str) -> bool:
        """向合辑批量添加内容（rids 逗号分隔）。rtype: 2 视频 / 3 文章。"""
        acer = self.require_acer()
        if acer.album is None:
            raise NotInCar()
        return bool(acer.album.contents_add(album_id, rtype, rids))

    def album_contents_del(self, album_id: Any, rids: str) -> bool:
        """从合辑批量移除内容（rids 逗号分隔）。"""
        acer = self.require_acer()
        if acer.album is None:
            raise NotInCar()
        return bool(acer.album.contents_del(album_id, rids))

    # ------------------------------------------------------------------
    # 举报
    # ------------------------------------------------------------------

    def report_content(self, rtype: Any, rid: Any, crime: str, proof: str, description: str) -> Any:
        """举报内容（视频/文章/番剧/动态）。crime 为举报类目文案。"""
        acer = self.require_acer()
        resource = acer.acfun.resource(rtype, rid)
        return resource.report(crime, proof, description)
