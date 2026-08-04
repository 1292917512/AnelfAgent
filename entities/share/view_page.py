"""分享预览页的服务端 HTML 模板。

预览页对外匿名可访问，不依赖前端构建产物，样式全部内联（深色极简风）。
- media 类型：按 media_kind 内嵌渲染（img/video/audio/iframe）+ 下载按钮
- link 类型：落地页——iframe 尝试嵌入预览 + 直接访问按钮（目标站点可能禁止嵌入）
"""

from __future__ import annotations

import time
from html import escape
from typing import Any, Dict

_PAGE_CSS = """
:root { color-scheme: dark; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: #0f1115; color: #e4e6eb; min-height: 100vh;
    display: flex; flex-direction: column; align-items: center;
}
.page { width: 100%; max-width: 980px; padding: 24px 16px; display: flex;
    flex-direction: column; gap: 16px; flex: 1; }
.head { display: flex; align-items: flex-start; justify-content: space-between;
    gap: 12px; flex-wrap: wrap; }
.head .meta { min-width: 0; flex: 1; }
.title { font-size: 16px; font-weight: 600; word-break: break-all; }
.desc { font-size: 13px; color: #9aa0a6; margin-top: 4px; word-break: break-all; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px;
    border-radius: 8px; background: #3b82f6; color: #fff; text-decoration: none;
    font-size: 13px; border: none; cursor: pointer; white-space: nowrap; }
.btn:hover { background: #2f6fdd; }
.btn.ghost { background: transparent; border: 1px solid #333a45; color: #e4e6eb; }
.btn.ghost:hover { background: #1a1f29; }
.media-box { flex: 1; display: flex; align-items: center; justify-content: center;
    background: #161a22; border: 1px solid #232833; border-radius: 12px;
    overflow: hidden; min-height: 320px; padding: 12px; }
.media-box img { max-width: 100%; max-height: 72vh; object-fit: contain;
    cursor: zoom-in; border-radius: 6px; }
.media-box video { max-width: 100%; max-height: 72vh; border-radius: 6px; }
.media-box audio { width: min(480px, 100%); }
.media-box iframe { width: 100%; height: 76vh; border: 0; border-radius: 6px;
    background: #fff; }
.media-fallback { display: none; flex-direction: column; align-items: center;
    gap: 12px; padding: 48px 16px; text-align: center; }
.media-fallback p { font-size: 13px; color: #9aa0a6; line-height: 1.7; max-width: 380px; }
/* 图片灯箱：全屏遮罩 + 点击/ESC 关闭 */
.lightbox { display: none; position: fixed; inset: 0; z-index: 100;
    background: rgba(0,0,0,.85); align-items: center; justify-content: center;
    cursor: zoom-out; }
.lightbox.show { display: flex; }
.lightbox img { max-width: 94vw; max-height: 92vh; border-radius: 6px; }
.tip { font-size: 12px; color: #9aa0a6; background: #161a22;
    border: 1px solid #232833; border-radius: 8px; padding: 10px 12px;
    line-height: 1.6; display: none; }
.tip.show { display: block; }
.foot { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap;
    font-size: 12px; color: #6b7280; }
.center { display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 12px; flex: 1; text-align: center; padding: 48px 0; }
.center h1 { font-size: 18px; }
.center p { font-size: 13px; color: #9aa0a6; max-width: 420px; line-height: 1.7; }
"""


def _page_shell(title: str, body: str) -> str:
    """组装完整 HTML 页面。"""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex">\n'
        f"<title>{escape(title)}</title>\n"
        f"<style>{_PAGE_CSS}</style>\n"
        f"</head>\n<body>\n<div class=\"page\">\n{body}\n</div>\n</body>\n</html>"
    )


def _head_block(entry: Dict[str, Any], actions_html: str = "") -> str:
    """标题 + 描述 + 操作按钮区。"""
    desc = entry.get("description", "")
    desc_html = f'<div class="desc">{escape(desc)}</div>' if desc else ""
    return (
        '<div class="head">\n'
        '  <div class="meta">\n'
        f'    <div class="title">{escape(entry.get("file_name", ""))}</div>\n'
        f"    {desc_html}\n"
        "  </div>\n"
        f"  {actions_html}\n"
        "</div>"
    )


def _foot_block(entry: Dict[str, Any]) -> str:
    """页脚：文件大小 + 到期时间。"""
    parts: list[str] = []
    size = int(entry.get("file_size", 0) or 0)
    if size > 0:
        if size < 1024:
            parts.append(f"{size} B")
        elif size < 1024 * 1024:
            parts.append(f"{size / 1024:.1f} KB")
        else:
            parts.append(f"{size / 1024 / 1024:.1f} MB")
    expires_at = int(entry.get("expires_at", 0) or 0)
    if expires_at > 0:
        parts.append("有效期至 " + time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(expires_at / 1000)))
    else:
        parts.append("永久有效")
    return f'<div class="foot"><span>{" · ".join(parts)}</span><span>AnelfAgent 分享</span></div>'


# 视频加载失败的兜底提示（onerror 触发，浏览器不支持该封装格式时展示）
_MEDIA_FALLBACK = (
    '<div class="media-fallback">'
    "<p>该格式不支持在线播放（浏览器不支持此封装/编码），请下载后观看。</p>"
    '<a class="btn" href="{dl}" download>下载文件</a>'
    "</div>"
)

# 灯箱 overlay + 视频 onerror 兜底的注入脚本
_MEDIA_SCRIPT = """
<div class="lightbox" id="lb" onclick="this.classList.remove('show')"><img id="lbImg" alt=""></div>
<script>
(function() {
  // 图片灯箱：点击放大，点击遮罩或 ESC 关闭
  var box = document.getElementById('lb');
  var boxImg = document.getElementById('lbImg');
  document.querySelectorAll('.media-box img').forEach(function(im) {
    im.addEventListener('click', function() {
      boxImg.src = im.src;
      box.classList.add('show');
    });
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') box.classList.remove('show');
  });
  // 视频加载失败（浏览器不支持的封装/编码）→ 兜底提示 + 下载
  var v = document.querySelector('.media-box video');
  if (v) {
    v.addEventListener('error', function() {
      var fb = v.parentElement.querySelector('.media-fallback');
      if (fb) { v.style.display = 'none'; fb.style.display = 'flex'; }
    });
  }
})();
</script>
"""


def render_media_page(entry: Dict[str, Any], raw_url: str, download_url: str) -> str:
    """media 类型预览页：按 media_kind 内嵌渲染 + 下载按钮。"""
    kind = entry.get("media_kind", "")
    name = escape(entry.get("file_name", ""))
    raw = escape(raw_url, quote=True)
    dl = escape(download_url, quote=True)

    actions = f'<a class="btn" href="{dl}" download>下载</a>'
    if kind == "image":
        media_html = f'<img src="{raw}" alt="{name}">'
    elif kind == "video":
        # preload=metadata 只拉首帧信息；onerror 兜底浏览器不支持的封装格式
        media_html = (
            f'<video controls preload="metadata" src="{raw}"></video>'
            + _MEDIA_FALLBACK.replace("{dl}", dl)
        )
    elif kind == "audio":
        media_html = f'<audio controls src="{raw}"></audio>'
    else:  # pdf / html 走 iframe 内嵌
        media_html = f'<iframe src="{raw}" title="{name}"></iframe>'

    body = (
        _head_block(entry, actions)
        + f'\n<div class="media-box">{media_html}</div>\n'
        + _foot_block(entry)
        + "\n"
        + _MEDIA_SCRIPT
    )
    return _page_shell(entry.get("file_name", "分享预览"), body)


def render_link_page(entry: Dict[str, Any], embed_enabled: bool) -> str:
    """link 类型落地页：iframe 嵌入预览 + 直接访问按钮。

    目标站点可能通过 X-Frame-Options / CSP 禁止嵌入，且浏览器侧无法可靠
    区分「已加载」与「被拦截」，因此常驻一条提示引导用户使用直接访问。
    """
    url = entry.get("target_url", "")
    safe_url = escape(url, quote=True)

    actions = f'<a class="btn" href="{safe_url}" target="_blank" rel="noopener">直接访问</a>'
    embed_html = ""
    if embed_enabled and url:
        embed_html = (
            f'\n<iframe src="{safe_url}" title="站点预览"></iframe>'
            '\n<div class="tip show">若上方预览为空白，说明目标站点禁止被嵌入，'
            "请点「直接访问」在新窗口打开。</div>"
        )

    body = _head_block(entry, actions) + embed_html + "\n" + _foot_block(entry)
    return _page_shell(entry.get("file_name", "网址分享"), body)


def render_unavailable_page(message: str) -> str:
    """链接失效（过期/撤销/次数耗尽/不存在）提示页。"""
    body = (
        '<div class="center">\n'
        "  <h1>链接不可用</h1>\n"
        f"  <p>{escape(message)}</p>\n"
        "</div>"
    )
    return _page_shell("链接不可用", body)
