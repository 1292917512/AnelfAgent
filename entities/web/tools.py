"""网络工具实体 — 检索、网页读取、仓库文档、HTTP 请求（接口层）。

本文件只做工具注册与参数校验，能力实现全部在 providers/ 模块
（能力 × 提供者矩阵：Provider 抽象 + builtin/minimax/bigmodel 具体实现），
经 providers.resolve 统一解析；直连抓取设施在 fetcher.py。

工具面：
- web_search:      检索关键词，返回结构化结果列表（可指定提供者）
- web_fetch:       读取指定 URL 的可读正文（可指定提供者；分块统一在工具层）
- repo_docs:       GitHub 仓库知识文档 / 目录结构 / 文件内容（check_fn 门控：无可用提供者不出现在 schema）
- web_providers:   提供者管理（矩阵查看 / 切换 / 启停 / 凭据配置）
- web_request:     通用 HTTP 请求（GET/POST，自定义 Header）
- extract_page_links: 提取页面所有链接
- web_download:    下载远程文件到本地 workspace（按需落盘）

Model Experience（新增工具）：
① 模型看到什么 — web_providers（web 组管理工具）与 repo_docs（有可用仓库
   提供者时才注入 schema）；凭据不回显；
② token 影响 — repo_docs 仅在门控通过时增加一个工具 schema；调用结果按
   result_budget 既有规则截断；
③ 缓存影响 — 纯工具层，不触碰任何 prompt 分层内容，前缀无感。
"""

from __future__ import annotations

import json
from typing import Optional

from entities._sdk import (
    ErrorCause,
    entity,
    entity_manifest,
    error_from_exception,
    tool,
    tool_error,
)

entity("web", "网络工具 - 联网检索、网页读取、仓库文档、HTTP 请求")
entity_manifest(
    display_name="网络工具",
    icon="globe",
    description="联网检索、网页正文读取、GitHub 仓库文档、HTTP 请求、文件下载",
    version="1.0.0",
    group="web",
)

# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_WEB_CONFIGS = {
    "entity/web": {
        "web_ssrf_protection": {
            "description": "是否开启 SSRF 防护（拒绝访问回环/内网/链路本地等受限地址）",
            "default": True,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_WEB_CONFIGS)


def _repo_available() -> bool:
    """repo_docs 门控：存在可用的仓库文档提供者才注入工具 schema。"""
    from entities.web import providers
    from entities.web.providers.base import CAP_REPO
    return providers.any_available(CAP_REPO)


# ==================================================================
# 检索
# ==================================================================


@tool(name="web_search", group="web", tags=["web"], concurrency_safe=True)
def web_search(query: str, max_results: int = 8, provider: str = "") -> str:
    """搜索关键词，返回结构化结果列表（标题/链接/摘要）。

    经当前配置的检索提供者查询全网信息。若需要某个页面的完整内容，请配合
    web_fetch 使用。时间敏感的问题（比分、新闻、股价等）：query 中应显式
    包含当前日期/年份等时间词（如"2026年7月 世界杯决赛比分"），避免搜出过时内容。

    Args:
        query:       搜索关键词，支持自然语言；时间敏感问题请显式写入日期/年份
        max_results: 最多返回条数，默认 8，最大 20
        provider:    指定检索提供者（如 minimax / bigmodel），留空用系统当前选择
    """
    from entities.web import providers
    from entities.web.providers.base import CAP_SEARCH, SearchCap
    max_results = min(max(1, max_results), 20)
    try:
        selected = providers.resolve(CAP_SEARCH, provider)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False)
    if not isinstance(selected, SearchCap):
        return tool_error(f"提供者 {selected.name} 不支持检索能力", cause=ErrorCause.CONFIG, retryable=False)
    try:
        output = selected.search(query.strip(), max_results)
        output["provider"] = selected.name
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        return selected.error_response(e, "网页搜索")


# ==================================================================
# 网页读取
# ==================================================================


@tool(name="web_fetch", group="web", tags=["web"], concurrency_safe=True)
def web_fetch(
    url: str,
    extract_mode: str = "markdown",
    max_chars: int = 8000,
    timeout: int = 15,
    use_proxy: bool = False,
    start_index: int = 0,
    respect_robots: bool = False,
    provider: str = "",
) -> str:
    """获取指定 URL 的网页正文，自动提取可读内容。

    长页面会按 max_chars 截断；返回 truncated=true 时，
    可用 start_index 传回 next_start_index 继续分块读取后续内容。

    Args:
        url:            网页地址（必须以 http:// 或 https:// 开头）
        extract_mode:   输出格式：markdown（默认，保留结构）、text（纯文本）或 raw（原始内容，仅 builtin 提供者支持）
        max_chars:      最大返回字符数，默认 8000
        timeout:        超时秒数，默认 15
        use_proxy:      是否使用代理（仅 builtin 提供者生效），默认 False
        start_index:    从该字符索引开始返回，默认 0，用于长页面分块续读
        respect_robots: 是否遵守目标站点 robots.txt 合规检查（仅 builtin 提供者生效），默认 False
        provider:       指定读取提供者（builtin 本地直连 / bigmodel 智谱），留空用系统当前选择
    """
    from entities.web import providers
    from entities.web.content_extractor import truncate_text
    from entities.web.providers.base import CAP_READER, ReaderCap

    max_chars = int(max_chars)
    start_index = max(0, int(start_index))
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": f"仅支持 http/https，收到: {url[:50]}"}, ensure_ascii=False)

    try:
        selected = providers.resolve(CAP_READER, provider)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False)
    if not isinstance(selected, ReaderCap):
        return tool_error(f"提供者 {selected.name} 不支持网页读取能力", cause=ErrorCause.CONFIG, retryable=False)
    try:
        result = selected.read(
            url, timeout=int(timeout), extract_mode=extract_mode,
            use_proxy=use_proxy, respect_robots=respect_robots,
        )
    except Exception as e:
        return selected.error_response(e, f"读取 {url}")

    # 分块截断统一在工具层施加，与提供者无关
    content = str(result.get("content", ""))[start_index:]
    content, truncated = truncate_text(content, max_chars)
    result["content"] = content
    result["truncated"] = truncated
    if truncated:
        result["next_start_index"] = start_index + len(content)
    result["provider"] = selected.name
    return json.dumps(result, ensure_ascii=False)


# ==================================================================
# 仓库文档
# ==================================================================


@tool(name="repo_docs", concurrency_safe=True, group="web", tags=["web"], check_fn=_repo_available)
def repo_docs(
    action: str,
    repo: str,
    query: str = "",
    path: str = "",
    dir_path: str = "",
    provider: str = "",
) -> str:
    """查询 GitHub 开源仓库的知识文档、目录结构与文件内容（如智谱 ZRead）。

    适合了解某个开源项目的架构、用法、最近 issue/PR，或直接阅读仓库文件，
    无需克隆仓库。

    Args:
        action:   search_doc（搜索仓库知识文档，需 query）/ get_structure（目录结构，可选 dir_path）/ read_file（文件内容，需 path）
        repo:     仓库标识 owner/repo（如 "vitejs/vite"，GitHub URL 亦可）
        query:    search_doc 的检索问题或关键词
        path:     read_file 的文件相对路径（如 "src/index.ts"）
        dir_path: get_structure 的子目录路径（留空为根目录）
        provider: 指定提供者，留空用系统当前选择
    """
    from entities.web import providers
    from entities.web.providers.base import CAP_REPO, RepoCap

    action = action.strip().lower()
    repo = repo.strip().removeprefix("https://github.com/").strip("/")
    if not repo or "/" not in repo:
        return tool_error(f"repo 需要 owner/repo 格式，收到: {repo or '(空)'}", cause=ErrorCause.PARAM, retryable=False)

    try:
        selected = providers.resolve(CAP_REPO, provider)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False)
    if not isinstance(selected, RepoCap):
        return tool_error(f"提供者 {selected.name} 不支持仓库文档能力", cause=ErrorCause.CONFIG, retryable=False)

    try:
        if action == "search_doc":
            if not query.strip():
                return tool_error("search_doc 需要 query 参数", cause=ErrorCause.PARAM, retryable=False)
            content = selected.search_doc(repo, query.strip())
        elif action == "get_structure":
            content = selected.get_repo_structure(repo, dir_path.strip())
        elif action == "read_file":
            if not path.strip():
                return tool_error("read_file 需要 path 参数", cause=ErrorCause.PARAM, retryable=False)
            content = selected.read_repo_file(repo, path.strip())
        else:
            return tool_error(
                f"未知操作: {action}（可选: search_doc / get_structure / read_file）",
                cause=ErrorCause.PARAM, retryable=False,
            )
    except Exception as e:
        return selected.error_response(e, f"仓库文档 {action}")

    return json.dumps({
        "repo": repo, "action": action, "provider": selected.name, "content": content,
    }, ensure_ascii=False)


# ==================================================================
# 提供者管理
# ==================================================================


@tool(name="web_providers", group="web", tags=["web", "core"])
def web_providers(action: str = "list", provider: str = "", capability: str = "", api_key: str = "") -> str:
    """管理网络能力提供者：查看能力矩阵、切换能力实现、启停提供者、配置凭据。

    Args:
        action:     list（能力 × 提供者矩阵，默认）/ switch（切换某能力的提供者，provider=auto 恢复自动选择）/ enable / disable（启停提供者）/ set_key（配置凭据，api_key 传 clear 清除）
        provider:   目标提供者名（如 builtin / minimax / bigmodel）
        capability: 目标能力（switch 必填：search 检索 / reader 网页读取 / repo 仓库文档）
        api_key:    凭据内容（set_key 必填；传 clear 清除已存凭据）
    """
    from entities.web import providers
    from entities.web.providers.base import CAPABILITY_PROTOCOLS
    from entities.web.web_config import set_active, set_enabled

    action = action.strip().lower()

    if action == "list":
        return json.dumps(_providers_matrix(), ensure_ascii=False)

    if action == "switch":
        cap = capability.strip()
        if cap not in CAPABILITY_PROTOCOLS:
            return tool_error(
                f"未知能力: {cap or '(空)'}（可选: {', '.join(CAPABILITY_PROTOCOLS)}）",
                cause=ErrorCause.PARAM, retryable=False,
            )
        name = provider.strip()
        if not name:
            return tool_error("switch 需要提供 provider 参数", cause=ErrorCause.PARAM, retryable=False)
        if name != "auto":
            try:
                providers.resolve(cap, name)  # 校验：不支持/已禁用/未配置会带原因抛出
            except ValueError as e:
                return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False)
        set_active(cap, name)
        return json.dumps({
            "status": "ok", "capability": cap, "selection": name,
            "active": _active_map(),
        }, ensure_ascii=False)

    if action in ("enable", "disable"):
        name = provider.strip()
        if not name:
            return tool_error(f"{action} 需要提供 provider 参数", cause=ErrorCause.PARAM, retryable=False)
        try:
            providers.get_provider(name)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False)
        set_enabled(name, action == "enable")
        return json.dumps({
            "status": "ok", "provider": name, "enabled": action == "enable",
            "active": _active_map(),
        }, ensure_ascii=False)

    if action == "set_key":
        name = provider.strip()
        if not name:
            return tool_error("set_key 需要提供 provider 参数", cause=ErrorCause.PARAM, retryable=False)
        try:
            target = providers.get_provider(name)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False)
        if not target.requires_credential:
            return tool_error(f"提供者 {name} 无需凭据", cause=ErrorCause.PARAM, retryable=False)
        key = api_key.strip()
        if not key:
            return tool_error(
                "set_key 需要提供 api_key 参数（传 clear 清除已存凭据）",
                cause=ErrorCause.PARAM, retryable=False,
            )
        try:
            target.set_api_key("" if key.lower() == "clear" else key)
        except Exception as e:
            return error_from_exception(e, action=f"保存 {name} 凭据")
        return json.dumps({
            "status": "ok",
            "provider": name,
            "credential": "cleared" if key.lower() == "clear" else "saved",
            "configured": target.configured(),
        }, ensure_ascii=False)

    return tool_error(
        f"未知操作: {action}（可选: list / switch / enable / disable / set_key）",
        cause=ErrorCause.PARAM, retryable=False,
    )


def _active_map() -> dict:
    """各能力当前生效的提供者（无可用则为 null）。"""
    from entities.web import providers
    from entities.web.providers.base import CAPABILITY_PROTOCOLS
    result = {}
    for cap in CAPABILITY_PROTOCOLS:
        try:
            result[cap] = providers.resolve(cap).name
        except ValueError:
            result[cap] = None
    return result


def _providers_matrix() -> dict:
    """能力 × 提供者矩阵快照（凭据只暴露来源，不回显本体）。"""
    from entities.web import providers
    from entities.web.providers.base import CAPABILITY_PROTOCOLS
    from entities.web.web_config import get_active
    return {
        "capabilities": list(CAPABILITY_PROTOCOLS),
        "selection": {cap: get_active(cap) for cap in CAPABILITY_PROTOCOLS},
        "active": _active_map(),
        "providers": [
            {
                "name": p.name,
                "display_name": p.display_name,
                "description": p.description,
                "enabled": p.enabled(),
                "configured": p.configured(),
                "credential_source": p.credential()[1] or None,
                "capabilities": providers.provider_capabilities(p),
            }
            for p in providers.list_providers()
        ],
        "hint": "switch 切换能力实现（provider=auto 自动选择）；enable/disable 启停提供者；set_key 配置凭据",
    }


# ==================================================================
# 页面链接提取（直连语义）
# ==================================================================


@tool(name="extract_page_links", group="web", tags=["web"], concurrency_safe=True)
def extract_page_links(
    url: str,
    max_links: int = 50,
    timeout: int = 15,
    use_proxy: bool = False,
) -> str:
    """提取指定网页中的所有超链接（URL + 链接文本）。

    Args:
        url:       要分析的网页 URL
        max_links: 最多返回链接数，默认 50
        timeout:   超时秒数，默认 15
        use_proxy: 是否使用代理，默认 False
    """
    import httpx

    from entities.web.fetcher import fetch
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "仅支持 http/https"}, ensure_ascii=False)
    try:
        _status, final_url, content_type, body = fetch(
            url, timeout=float(timeout), use_proxy=use_proxy)
        if "text/html" not in content_type:
            return json.dumps({"error": f"非 HTML 页面: {content_type}"}, ensure_ascii=False)
        from entities.web.content_extractor import extract_links
        links = extract_links(body, base_url=final_url)
        total = len(links)
        return json.dumps({"url": final_url, "total_links": total, "returned": min(total, max_links), "links": links[:max_links]}, ensure_ascii=False)
    except httpx.TimeoutException as e:
        return error_from_exception(e, action=f"请求 {url}")
    except Exception as e:
        return error_from_exception(e, action=f"请求 {url}")


# ==================================================================
# 通用 HTTP 请求（直连语义）
# ==================================================================


@tool(name="web_request", group="web", tags=["web"], concurrency_safe=True)
def web_request(
    url: str,
    method: str = "GET",
    body: str = "",
    headers: str = "",
    timeout: int = 15,
    max_chars: int = 5000,
    use_proxy: bool = False,
) -> str:
    """发送 HTTP 请求，返回状态码和响应体。

    适合调用 API、表单提交等场景。

    Args:
        url:       完整 URL
        method:    HTTP 方法，GET（默认）、POST、PUT、DELETE、PATCH
        body:      请求体（POST/PUT 时使用，JSON 字符串）
        headers:   额外请求头（JSON 格式，如 {\"Authorization\": \"Bearer token\"}）
        timeout:   超时秒数，默认 15
        max_chars: 响应体最大字符数，默认 5000
        use_proxy: 是否使用代理，默认 False
    """
    import httpx

    from entities.web.fetcher import fetch
    method = method.upper()
    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        return json.dumps({"error": f"不支持的方法: {method}"}, ensure_ascii=False)
    req_headers = {}
    if headers.strip():
        try:
            req_headers = json.loads(headers)
        except Exception:
            return json.dumps({"error": f"headers JSON 解析失败: {headers}"}, ensure_ascii=False)
    req_body: Optional[str] = body.strip() or None

    try:
        _status, _final_url, content_type, text = fetch(
            url, method=method, content=req_body,
            headers=req_headers or None, timeout=float(timeout), use_proxy=use_proxy,
        )
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n... (响应过长，已截断)"
        return json.dumps({
            "status_code": _status,
            "content_type": content_type,
            "body": text,
            "truncated": truncated,
        }, ensure_ascii=False)
    except httpx.TimeoutException as e:
        return error_from_exception(e, action=f"请求 {url}")
    except PermissionError as e:
        return error_from_exception(e, action=f"请求 {url}")
    except Exception as e:
        return error_from_exception(e, action=f"请求 {url}")


# ==================================================================
# 文件下载（直连语义）
# ==================================================================


@tool(name="web_download", group="web",
      tags=["web", "media:file", "media:video", "media:audio", "media:voice"],
      concurrency_safe=True,
      # 下载场景的慢链路预算：AI 参数 timeout（默认 30s）在此范围内生效，
      # 未声明则落入全局默认 60s，AI 传 120+ 秒也会被提前掐断（死配置）
      timeout=300.0)
def web_download(
    url: str,
    filename: str = "",
    max_mb: int = 50,
    timeout: int = 30,
    use_proxy: bool = False,
) -> str:
    """下载远程文件到本地 workspace/uploads/file/，返回本地路径。

    适用于频道消息中标记为「未下载」且标签带有 URL 的媒体/文件，
    或任何需要落地后再用 read_file 等工具分析的远程文件。
    若标签带的是 file_id 而非 URL（如 QQ 文件），请改用 qq_download_file。

    Args:
        url:       文件地址（必须以 http:// 或 https:// 开头）
        filename:  期望保存的文件名（可选，默认从 URL 推断）
        max_mb:    允许的最大文件大小（MB），默认 50
        timeout:   超时秒数，默认 30
        use_proxy: 是否使用代理，默认 False
    """
    import os
    import time
    import uuid
    from urllib.parse import unquote, urlparse

    import httpx

    from core.config import ConfigManager
    from entities.web.fetcher import download_to_file

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": f"仅支持 http/https，收到: {url[:50]}"}, ensure_ascii=False)
    max_bytes = max(1, int(max_mb)) * 1024 * 1024

    try:
        ws = ConfigManager.get("workspace_root", "workspace")
    except Exception:
        ws = "workspace"
    dl_dir = os.path.abspath(os.path.join(ws, "uploads", "file"))
    os.makedirs(dl_dir, exist_ok=True)

    name = os.path.basename(filename.strip()) if filename.strip() else ""
    if not name:
        name = os.path.basename(unquote(urlparse(url).path)) or "download.bin"
    local_path = os.path.join(
        dl_dir, f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}_{name}")
    if not os.path.realpath(local_path).startswith(os.path.realpath(dl_dir) + os.sep):
        return json.dumps({"error": f"非法文件名: {filename}"}, ensure_ascii=False)

    try:
        _status, final_url, written = download_to_file(
            url, local_path, max_bytes, timeout=float(timeout), use_proxy=use_proxy)
    except httpx.TimeoutException as e:
        return error_from_exception(e, action=f"下载 {url}")
    except PermissionError as e:
        return error_from_exception(e, action=f"下载 {url}")
    except Exception as e:
        return error_from_exception(e, action=f"下载 {url}")

    return json.dumps({
        "path": local_path,
        "name": os.path.basename(local_path),
        "size": written,
        "source_url": final_url,
        "hint": "文件已下载，可用 read_file 读取该路径进行分析",
    }, ensure_ascii=False)
