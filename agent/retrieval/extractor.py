"""网页内容提取工具。

三层提取管线：
  1. BS4 预清洗 — 移除导航、广告、弹窗等噪声节点
  2. Readability — 从净化后的 HTML 提取正文区块
  3. BS4 评分兜底 — Readability 失效时按文字密度评分找主内容区

同时提供页面元数据提取（标题/描述/作者/日期）和链接提取。

依赖：readability-lxml, markdownify, lxml, beautifulsoup4
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from core.log import log

# ------------------------------------------------------------------
# 噪声选择器：匹配导航、广告、弹窗、侧边栏等无关区域
# ------------------------------------------------------------------

_NOISE_TAGS = frozenset({
    "script", "style", "noscript", "iframe", "svg", "canvas",
    "header", "footer", "nav", "aside", "form",
})

_NOISE_PATTERNS = re.compile(
    r"(ad(s|vert(isement)?)?|banner|popup|modal|cookie|gdpr|consent|"
    r"sidebar|side.bar|nav(bar|igation)?|breadcrumb|menu|toolbar|"
    r"related|recommend|comment|social|share|like|follow|subscribe|"
    r"newsletter|widget|promo|sponsored|float|sticky|overlay|"
    r"header|footer|topbar|bottom.bar|pagination|pager)",
    re.IGNORECASE,
)


def _is_noisy(tag: Any) -> bool:
    """判断 BS4 元素是否为噪声节点。"""
    if tag is None or not hasattr(tag, "get"):
        return False
    for attr in ("id", "class", "role"):
        try:
            val = tag.get(attr, "")
        except Exception:
            continue
        if val is None:
            val = ""
        text = " ".join(val) if isinstance(val, list) else str(val)
        if _NOISE_PATTERNS.search(text):
            return True
    return False


# ==================================================================
# 公开 API
# ==================================================================


def extract_page_metadata(html: str, url: str = "") -> Dict[str, str]:
    """提取页面元数据：标题、描述、作者、发布时间、关键词。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {}

    soup = BeautifulSoup(html, "lxml" if _has_lxml() else "html.parser")
    meta: Dict[str, str] = {}

    # 标题
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        meta["title"] = og_title["content"].strip()
    elif soup.title:
        meta["title"] = soup.title.get_text(strip=True)

    # 描述
    for sel in [{"property": "og:description"}, {"name": "description"}, {"name": "twitter:description"}]:
        tag = soup.find("meta", attrs=sel)
        if tag and tag.get("content"):
            meta["description"] = tag["content"].strip()
            break

    # 作者
    for sel in [{"name": "author"}, {"property": "article:author"}]:
        tag = soup.find("meta", attrs=sel)
        if tag and tag.get("content"):
            meta["author"] = tag["content"].strip()
            break
    if "author" not in meta:
        for cls in ("author", "byline", "post-author"):
            tag = soup.find(class_=re.compile(cls, re.I))
            if tag:
                meta["author"] = tag.get_text(strip=True)[:100]
                break

    # 发布时间
    for sel in [{"property": "article:published_time"}, {"name": "pubdate"}, {"itemprop": "datePublished"}]:
        tag = soup.find("meta", attrs=sel) or soup.find(attrs=sel)
        if tag:
            v = tag.get("content") or tag.get("datetime") or tag.get_text(strip=True)
            if v:
                meta["published"] = v.strip()[:50]
                break

    # 关键词
    kw_tag = soup.find("meta", attrs={"name": "keywords"})
    if kw_tag and kw_tag.get("content"):
        meta["keywords"] = kw_tag["content"].strip()[:200]

    # 规范 URL
    og_url = soup.find("meta", property="og:url") or soup.find("link", rel="canonical")
    if og_url:
        meta["canonical_url"] = (og_url.get("content") or og_url.get("href") or "").strip()

    return meta


def preprocess_html(html: str) -> str:
    """用 BS4 清洗 HTML：移除噪声节点，保留语义结构。

    返回净化后的 HTML 字符串，用于喂给 Readability。
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return html

    parser = "lxml" if _has_lxml() else "html.parser"
    soup = BeautifulSoup(html, parser)

    # 1. 删除噪声标签
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

    # 2. 删除匹配噪声模式的任意节点
    for tag in soup.find_all(True):
        if _is_noisy(tag):
            tag.decompose()

    # 3. 移除所有内联事件属性和 style，保留语义 class
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.startswith("on") or attr == "style":
                del tag.attrs[attr]

    return str(soup)


def extract_readable_content(html: str, url: str = "") -> Optional[Tuple[str, str]]:
    """提取网页正文（BS4 预清洗 → Readability → BS4 评分兜底）。

    Returns:
        (title, content_html) 或 None（完全失败时）
    """
    clean_html = preprocess_html(html)

    # --- 第一选择：Readability ---
    try:
        from readability import Document
        doc = Document(clean_html, url=url)
        title = doc.short_title() or ""
        content_html = doc.summary(html_partial=True)
        # Readability 有时返回近空页面，兜底检测
        text_len = len(re.sub(r"<[^>]+>", "", content_html))
        if text_len > 100:
            return title, content_html
        log("Readability 正文过短，尝试 BS4 评分兜底", "DEBUG")
    except ImportError:
        log("readability-lxml 未安装，降级到 BS4 评分提取", "WARNING")
    except Exception as e:
        log(f"Readability 失败: {e}，尝试 BS4 兜底", "WARNING")

    # --- 兜底：BS4 文字密度评分 ---
    return _bs4_extract(clean_html, url)


def html_to_markdown(html: str) -> str:
    """HTML → Markdown，后处理去除多余空行和无意义符号。"""
    try:
        from markdownify import markdownify as md
        text = md(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "img", "figure", "svg"],
            newline_style="backslash",
        )
    except ImportError:
        log("markdownify 未安装，降级为 BS4 文本提取", "WARNING")
        return _bs4_to_text(html) or _simple_html_to_text(html)

    # 后处理
    text = re.sub(r"\[([^\]]*)\]\(\s*\)", r"\1", text)      # 移除空链接 [text]()
    text = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", "", text)  # 移除图片链接
    text = re.sub(r"\\+\n", "\n", text)                      # 消除多余反斜杠换行
    text = re.sub(r"\n{3,}", "\n\n", text)                   # 压缩多空行
    text = re.sub(r"[ \t]+\n", "\n", text)                   # 行尾空格
    text = re.sub(r"\n[ \t]+", "\n", text)                   # 行首空格
    return text.strip()


def extract_links(html: str, base_url: str = "", filter_noise: bool = True) -> List[Dict[str, str]]:
    """从 HTML 提取超链接，可选过滤导航/工具性链接。

    Returns:
        [{"url": "...", "text": "...", "context": "..."}]
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _regex_extract_links(html, base_url)

    parser = "lxml" if _has_lxml() else "html.parser"
    soup = BeautifulSoup(html, parser)
    links: List[Dict[str, str]] = []
    seen: set[str] = set()

    # 如果过滤噪声，先把噪声区域的链接排除掉
    noise_containers: set = set()
    if filter_noise:
        for tag in soup.find_all(True):
            if _is_noisy(tag):
                noise_containers.add(id(tag))

    def _in_noise(tag: Any) -> bool:
        parent = tag.parent
        while parent:
            if id(parent) in noise_containers:
                return True
            parent = parent.parent
        return False

    for a in soup.find_all("a", href=True):
        if filter_noise and _in_noise(a):
            continue

        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        if base_url:
            href = urljoin(base_url, href)
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue
        if href in seen:
            continue
        seen.add(href)

        text = a.get_text(strip=True)[:200]
        if not text:
            continue

        # 简单的锚文本质量过滤（跳过纯符号/短到无意义的链接）
        if filter_noise and len(text) < 2:
            continue

        # 获取周围上下文（父级段落或 li）
        context = ""
        for ancestor in a.parents:
            if ancestor.name in ("p", "li", "td", "dd"):
                ctx = ancestor.get_text(separator=" ", strip=True)
                if len(ctx) > len(text) + 5:
                    context = ctx[:150]
                break

        links.append({"url": href, "text": text, "context": context})

    return links


def truncate_text(text: str, max_chars: int) -> Tuple[str, bool]:
    """在段落边界处截断文本到指定字符数。"""
    if len(text) <= max_chars:
        return text, False
    truncated = text[:max_chars]
    # 优先在段落换行处截断
    last_para = truncated.rfind("\n\n", max(0, max_chars - 500))
    if last_para > max_chars * 0.7:
        return truncated[:last_para].rstrip(), True
    # 其次在句子末尾截断
    for punct in ("。", ".", "！", "!", "？", "?", "\n"):
        pos = truncated.rfind(punct, max(0, max_chars - 200))
        if pos > max_chars * 0.8:
            return truncated[:pos + 1].rstrip(), True
    return truncated.rstrip(), True


# ==================================================================
# 内部工具
# ==================================================================


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False


def _bs4_extract(html: str, url: str = "") -> Optional[Tuple[str, str]]:
    """BS4 文字密度评分：找到文字最密集的主容器作为正文。"""
    try:
        from bs4 import BeautifulSoup, Tag
    except ImportError:
        return None

    parser = "lxml" if _has_lxml() else "html.parser"
    soup = BeautifulSoup(html, parser)

    title = soup.title.get_text(strip=True) if soup.title else ""

    # 候选标签：article / main / section / div（按语义优先级排序）
    candidates: List[Tuple[float, Any]] = []

    # 先找语义标签
    for tag_name in ("article", "main", "[role=main]"):
        for el in soup.select(tag_name):
            score = _text_density(el)
            if score > 0.2:
                candidates.append((score + 10, el))  # 语义加权

    # 再评分所有块元素
    for el in soup.find_all(["div", "section", "td"]):
        if isinstance(el, Tag):
            score = _text_density(el)
            if score > 0.3:
                candidates.append((score, el))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1]
    return title, str(best)


def _text_density(tag: Any) -> float:
    """文字密度 = 纯文本字符数 / HTML 总字符数（含标签）。"""
    html_len = len(str(tag))
    if html_len < 100:
        return 0.0
    text_len = len(tag.get_text(strip=True))
    return text_len / html_len


def _bs4_to_text(html: str) -> str:
    """用 BS4 直接提取结构化纯文本（markdownify 不可用时的降级）。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _simple_html_to_text(html)

    parser = "lxml" if _has_lxml() else "html.parser"
    soup = BeautifulSoup(html, parser)

    # 给块级标签加换行
    for tag in soup.find_all(["p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]):
        tag.insert_before("\n")
        tag.insert_after("\n")

    text = soup.get_text(separator=" ")
    text = re.sub(r" +", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _simple_html_to_text(html: str) -> str:
    """极简正则 HTML 转文本（无任何外部依赖的最终兜底）。"""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|li|tr)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    for ent, rep in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')]:
        text = text.replace(ent, rep)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _regex_extract_links(html: str, base_url: str = "") -> List[Dict[str, str]]:
    """正则提取链接（beautifulsoup4 未安装时的兜底）。"""
    pattern = r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
    links: List[Dict[str, str]] = []
    seen: set[str] = set()

    for match in re.finditer(pattern, html, re.IGNORECASE | re.DOTALL):
        href = match.group(1).strip()
        text = re.sub(r"<[^>]+>", "", match.group(2)).strip()[:200]
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        if base_url:
            href = urljoin(base_url, href)
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue
        if href in seen:
            continue
        seen.add(href)
        if text:
            links.append({"url": href, "text": text, "context": ""})

    return links
