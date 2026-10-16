"""检索工具 — 联网检索、网页读取、仓库文档、HTTP 请求、文件下载、文档重排序。

接口层：本文件只做工具注册与参数校验，能力实现在 providers/ 模块
（能力 × 提供者矩阵：Provider 抽象、内置实现和组件注册），
经 providers.resolve 统一解析；本机抓取设施在 fetcher.py。
模型可见：工具参数 proxy_mode 选择全局网络策略；本地服务默认直连，HTTP 失败保留状态码与响应正文。
token 影响：失败回执增加少量归因字段，正文继续受长度预算约束。
缓存影响：不注入运行状态或代理配置到稳定上下文前缀。

工具面：
- web_search:        检索关键词，返回结构化结果列表（可指定提供者）
- web_fetch:         读取指定 URL 的可读正文（可指定提供者；分块统一在工具层）
- web_crawl:         浏览器服务渲染、CSS 等待和有界滚动（check_fn 门控）
- repo_docs:         GitHub 仓库知识文档 / 目录结构 / 文件内容（check_fn 门控）
- web_request:       通用 HTTP 请求（GET/POST，自定义 Header）
- extract_page_links: 提取页面所有链接
- web_download:      下载远程文件到本地 workspace（按需落盘）
- rerank_search:     按相关性对文档列表重排序（rerank 模型链）
- retrieval_providers: 提供者管理（矩阵查看 / 切换 / 启停 / 凭据配置）
- retrieval_services: 本地服务装配、状态、版本更新与回退
"""

from __future__ import annotations

import json
from typing import Optional

from core.entity import EntityRegistry
from core.network import ProxyMode
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from core.tool_registry import deferred_tool

EntityRegistry.register_group_order("retrieval", 30)

_group = "retrieval"


def _repo_available() -> bool:
    """repo_docs 门控：存在可用的仓库文档提供者才注入工具 schema。"""
    from agent.retrieval import providers
    from agent.retrieval.providers.base import CAP_REPO
    return providers.any_available(CAP_REPO)


def _crawl_available() -> bool:
    """仅配置了动态抓取提供者时向模型提供浏览器抓取工具。"""
    from agent.retrieval import providers
    from agent.retrieval.providers.base import CAP_CRAWL
    return providers.any_available(CAP_CRAWL)


@deferred_tool(name="retrieval_services", group=_group, tags=["web", "config"], timeout=30)
async def retrieval_services(action: str = "status", crawl4ai_image: str = "", searxng_image: str = "") -> str:
    """管理 Anelf 所在主机的 Crawl4AI/SearXNG 本地服务，所有操作与 Web 共用。

    status 返回 Docker 环境缺项、安装指引、容器状态和最近操作。缺少 Docker 时可按指引
    使用系统工具安装，安装后 up 自动初始化配置、拉取固定镜像并等待健康；首次下载较慢。
    写操作立即返回操作 ID，accepted 不代表成功，后续用 status 查询终态，不要重复提交。
    up 只填补空的 Anelf 服务地址；connect 显式替换为本地地址和凭据，不修改能力选择。
    配置服务地址、密钥、引擎与启动开关使用配置工具（retrieval/* 分组）。
    后续模型调用的动态区同步本进程操作状态；无需用 shell sleep 等待或重复提交。
    status 提供观察时刻、操作/阶段耗时、有界 Docker 输出及容器状态；available 只表示 CLI 和清单存在。

    Args:
        action: status/init/up/update/rollback/stop/connect；仅管理随项目部署的本地服务
        crawl4ai_image: update 时指定官方 unclecode/crawl4ai 镜像的明确标签或 sha256，空则不更新
        searxng_image: update 时指定官方 searxng/searxng 镜像的明确标签或 sha256，空则不更新
    """
    from agent.retrieval.deployment import get_deployment
    manager = get_deployment()
    try:
        if action == "status":
            return json.dumps(await manager.inspect(), ensure_ascii=False)
        images = {key: value for key, value in {"crawl4ai": crawl4ai_image, "searxng": searxng_image}.items() if value}
        return json.dumps(manager.start(action, images), ensure_ascii=False)
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.CONFIG, retryable=False)
    except RuntimeError as exc:
        return tool_error(str(exc), cause=ErrorCause.STATE, retryable=False)
    except Exception as exc:
        return error_from_exception(exc, action="管理本地检索服务")


@deferred_tool(name="web_search", group=_group, tags=["always", "web"], concurrency_safe=True)
def web_search(query: str, max_results: int = 8, provider: str = "") -> str:
    """搜索关键词，返回结构化结果列表（标题/链接/摘要）。

    经当前配置的检索提供者查询全网信息。若需要某个页面的完整内容，请配合
    web_fetch 使用。时间敏感的问题（比分、新闻、股价等）：query 中应显式
    包含当前日期/年份等时间词（如"2026年7月 世界杯决赛比分"），避免搜出过时内容。

    Args:
        query:       搜索关键词，支持自然语言；时间敏感问题请显式写入日期/年份
        max_results: 最多返回条数，默认 8，最大 20
        provider:    指定检索提供者，留空用系统当前选择
    """
    from agent.retrieval import providers
    from agent.retrieval.providers.base import CAP_SEARCH, SearchCap
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


@deferred_tool(name="web_fetch", group=_group, tags=["always", "web"], concurrency_safe=True)
def web_fetch(
    url: str,
    extract_mode: str = "markdown",
    max_chars: int = 8000,
    timeout: int = 15,
    proxy_mode: ProxyMode | None = None,
    start_index: int = 0,
    respect_robots: bool = False,
    provider: str = "",
) -> str:
    """获取指定 URL 的网页正文，自动提取可读内容。

    长页面会按 max_chars 截断；返回 truncated=true 时，
    可用 start_index 传回 next_start_index 继续分块读取后续内容。

    Args:
        url:            网页地址（必须以 http:// 或 https:// 开头）
        extract_mode:   输出格式：markdown（默认，保留结构）、text（纯文本）或 raw（原始内容，builtin/crawl4ai 支持）
        max_chars:      最大返回字符数，默认 8000
        timeout:        超时秒数，默认 15
        proxy_mode:     省略使用提供者默认策略（本地服务默认直连）；system 全局规则，direct 直连，proxy 全局代理。只控制本机到提供者/网站的网络
        start_index:    从该字符索引开始返回，默认 0，用于长页面分块续读
        respect_robots: 是否检查目标站点 robots.txt（builtin/crawl4ai 支持），默认 false
        provider:       指定读取提供者（builtin 轻量抓取 / crawl4ai 浏览器渲染 / bigmodel 智谱），留空用系统当前选择
    """
    from agent.retrieval import providers
    from agent.retrieval.extractor import truncate_text
    from agent.retrieval.providers.base import CAP_READER, ReaderCap

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
            proxy_mode=proxy_mode, respect_robots=respect_robots,
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


@deferred_tool(name="web_crawl", group=_group, tags=["always", "web"],
               concurrency_safe=True, check_fn=_crawl_available, timeout=60.0)
def web_crawl(
    url: str, css_selector: str = "", wait_for: str = "", scan_full_page: bool = False,
    extract_mode: str = "markdown", max_chars: int = 8000, start_index: int = 0,
    timeout: int = 30, proxy_mode: ProxyMode | None = None, respect_robots: bool = False,
    provider: str = "",
) -> str:
    """在本地浏览器服务渲染网页，等待元素、滚动加载并提取正文与链接。

    普通页面优先 web_fetch；依赖 JavaScript 渲染的内容使用本工具。
    不执行任意脚本、表单提交或跨调用登录会话。分块续读会重新抓取页面。

    Args:
        url: HTTP(S) 网页地址
        css_selector: 只提取指定 CSS 区域，留空读取全页
        wait_for: 等待出现的 CSS 选择器，留空在 DOM 就绪后提取
        scan_full_page: 是否滚动加载，最多 10 步
        extract_mode: markdown / text / raw
        max_chars: 正文返回上限，1–50000 字符
        start_index: 正文起点，返回 truncated 时用 next_start_index 续读
        timeout: 页面操作等待上限，1–45 秒
        proxy_mode: 到浏览器服务的连接策略，省略跟随服务配置（默认直连）；system 全局规则，direct 直连，proxy 全局代理。不改变浏览器出站
        respect_robots: 是否由浏览器服务检查 robots.txt
        provider: 动态抓取提供者，留空使用系统选择
    """
    from agent.retrieval import providers
    from agent.retrieval.extractor import truncate_text
    from agent.retrieval.providers.base import CAP_CRAWL, CrawlCap

    if not 1 <= max_chars <= 50000 or start_index < 0 or not 1 <= timeout <= 45:
        return tool_error("max_chars 须为 1–50000、start_index 非负、timeout 为 1–45 秒", cause=ErrorCause.PARAM, retryable=False)
    try:
        selected = providers.resolve(CAP_CRAWL, provider)
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.CONFIG, retryable=False)
    if not isinstance(selected, CrawlCap):
        return tool_error("提供者不支持动态抓取", cause=ErrorCause.CONFIG, retryable=False)
    try:
        result = selected.crawl(url.strip(), timeout=timeout, extract_mode=extract_mode,
            css_selector=css_selector, wait_for=wait_for, scan_full_page=scan_full_page,
            proxy_mode=proxy_mode, respect_robots=respect_robots)
    except Exception as exc:
        return selected.error_response(exc, "动态页面抓取")
    content, truncated = truncate_text(str(result.get("content", ""))[start_index:], max_chars)
    result.update(content=content, truncated=truncated, provider=selected.name)
    if truncated:
        result["next_start_index"] = start_index + len(content)
    return json.dumps(result, ensure_ascii=False)


# ==================================================================
# 仓库文档
# ==================================================================


@deferred_tool(name="repo_docs", group=_group, tags=["always", "web"], concurrency_safe=True, check_fn=_repo_available)
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
    from agent.retrieval import providers
    from agent.retrieval.providers.base import CAP_REPO, RepoCap

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


@deferred_tool(name="retrieval_providers", group=_group, tags=["core", "web"])
def retrieval_providers(action: str = "list", provider: str = "", capability: str = "", api_key: str = "") -> str:
    """管理检索能力提供者：查看能力矩阵、切换能力实现、启停提供者、配置凭据。

    Args:
        action:     list（能力 × 提供者矩阵，默认）/ switch（切换某能力的提供者，provider=auto 恢复自动选择）/ enable / disable（启停提供者）/ set_key（配置凭据，api_key 传 clear 清除）
        provider:   目标提供者名（如 builtin / searxng / crawl4ai / bigmodel）
        capability: 目标能力（switch 必填：search 检索 / reader 网页读取 / repo 仓库文档 / crawl 动态抓取）
        api_key:    凭据内容（set_key 必填；传 clear 清除已存凭据）
    """
    from agent.retrieval import providers
    from agent.retrieval.config import set_active, set_enabled
    from agent.retrieval.providers.base import CAPABILITY_PROTOCOLS

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
    from agent.retrieval import providers
    from agent.retrieval.providers.base import CAPABILITY_PROTOCOLS
    result: dict = {}
    for cap in CAPABILITY_PROTOCOLS:
        try:
            result[cap] = providers.resolve(cap).name
        except ValueError:
            result[cap] = None
    return result


def _providers_matrix() -> dict:
    """能力 × 提供者矩阵快照（凭据只暴露来源，不回显本体）。"""
    from agent.retrieval import providers
    from agent.retrieval.config import get_active
    from agent.retrieval.providers.base import CAPABILITY_PROTOCOLS
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
                "config_group": p.config_group,
                "credential_source": p.credential()[1] or None,
                "capabilities": providers.provider_capabilities(p),
            }
            for p in providers.list_providers()
        ],
        "hint": "switch 切换能力实现；enable/disable 启停；set_key 配置凭据；服务地址等参数通过 config_group 对应的统一配置工具编辑",
    }


# ==================================================================
# 页面链接提取（本机请求）
# ==================================================================


@deferred_tool(name="extract_page_links", group=_group, tags=["always", "web"], concurrency_safe=True)
def extract_page_links(
    url: str,
    max_links: int = 50,
    timeout: int = 15,
    proxy_mode: ProxyMode = "system",
) -> str:
    """提取指定网页中的所有超链接（URL + 链接文本）。

    Args:
        url:       要分析的网页 URL
        max_links: 最多返回链接数，默认 50
        timeout:   超时秒数，默认 15
        proxy_mode: system 按全局规则，direct 直连，proxy 使用全局代理，默认 system
    """
    import httpx

    from agent.retrieval.fetcher import fetch
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "仅支持 http/https"}, ensure_ascii=False)
    try:
        _status, final_url, content_type, body = fetch(
            url, timeout=float(timeout), proxy_mode=proxy_mode)
        if _status >= 400:
            return tool_error(
                f"读取网页失败（HTTP {_status}）", status_code=_status,
                cause=ErrorCause.PERMISSION if _status in (401, 403) else ErrorCause.INTERNAL,
                retryable=False,
            )
        if "text/html" not in content_type:
            return json.dumps({"error": f"非 HTML 页面: {content_type}"}, ensure_ascii=False)
        from agent.retrieval.extractor import extract_links
        links = extract_links(body, base_url=final_url)
        total = len(links)
        return json.dumps({"url": final_url, "total_links": total, "returned": min(total, max_links), "links": links[:max_links]}, ensure_ascii=False)
    except httpx.TimeoutException as e:
        return error_from_exception(e, action=f"请求 {url}")
    except Exception as e:
        return error_from_exception(e, action=f"请求 {url}")


# ==================================================================
# 通用 HTTP 请求（本机请求）
# ==================================================================


@deferred_tool(name="web_request", group=_group, tags=["always", "web"])
def web_request(
    url: str,
    method: str = "GET",
    body: str = "",
    headers: str = "",
    timeout: int = 15,
    max_chars: int = 5000,
    proxy_mode: ProxyMode = "system",
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
        proxy_mode: system 按全局规则，direct 直连，proxy 使用全局代理，默认 system
    """
    import httpx

    from agent.retrieval.fetcher import fetch
    method = method.strip().upper()
    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        return tool_error(f"不支持的方法: {method}", cause=ErrorCause.PARAM, retryable=False)
    if max_chars <= 0 or timeout <= 0:
        return tool_error("max_chars 和 timeout 必须为正数", cause=ErrorCause.PARAM, retryable=False)
    req_headers = {}
    if headers.strip():
        try:
            req_headers = json.loads(headers)
        except json.JSONDecodeError:
            return tool_error("headers 必须是 JSON 对象", cause=ErrorCause.PARAM, retryable=False)
        if not isinstance(req_headers, dict) or not all(isinstance(value, str) for value in req_headers.values()):
            return tool_error("headers 必须是字符串键值组成的 JSON 对象", cause=ErrorCause.PARAM, retryable=False)
    req_body: Optional[str] = body.strip() or None

    try:
        _status, _final_url, content_type, text = fetch(
            url, method=method, content=req_body,
            headers=req_headers or None, timeout=float(timeout), proxy_mode=proxy_mode,
        )
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n... (响应过长，已截断)"
        payload = {
            "status_code": _status,
            "content_type": content_type,
            "body": text,
            "truncated": truncated,
        }
        if _status >= 400:
            return tool_error(
                f"HTTP 请求失败（状态码 {_status}）",
                cause=ErrorCause.PERMISSION if _status in (401, 403) else ErrorCause.INTERNAL,
                retryable=False, code=f"HTTP_{_status}",
                hint="结合响应正文检查请求参数与目标状态；写请求失败不代表未生效，勿直接重复提交。",
                **payload,
            )
        return json.dumps(payload, ensure_ascii=False)
    except httpx.TimeoutException as e:
        return error_from_exception(e, action=f"请求 {url}")
    except PermissionError as e:
        return error_from_exception(e, action=f"请求 {url}")
    except Exception as e:
        return error_from_exception(e, action=f"请求 {url}")


# ==================================================================
# 文件下载（本机请求）
# ==================================================================


@deferred_tool(name="web_download", group=_group,
      tags=["always", "web", "media:file", "media:video", "media:audio", "media:voice"],
      concurrency_safe=True,
      # 下载场景的慢链路预算：AI 参数 timeout（默认 30s）在此范围内生效，
      # 未声明则落入全局默认 60s，AI 传 120+ 秒也会被提前掐断（死配置）
      timeout=300.0)
def web_download(
    url: str,
    filename: str = "",
    max_mb: int = 50,
    timeout: int = 30,
    proxy_mode: ProxyMode = "system",
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
        proxy_mode: system 按全局规则，direct 直连，proxy 使用全局代理，默认 system
    """
    import os
    import time
    import uuid
    from urllib.parse import unquote, urlparse

    import httpx

    from agent.retrieval.fetcher import download_to_file
    from agent.utils.workspace import get_workspace_root

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": f"仅支持 http/https，收到: {url[:50]}"}, ensure_ascii=False)
    max_bytes = max(1, int(max_mb)) * 1024 * 1024

    dl_dir = os.path.join(os.path.abspath(get_workspace_root()), "uploads", "file")
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
            url, local_path, max_bytes, timeout=float(timeout), proxy_mode=proxy_mode)
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


# ==================================================================
# 文档重排序
# ==================================================================


@deferred_tool(name="rerank_search", group=_group, tags=["core"], concurrency_safe=True)
async def rerank_search(query: str, documents: str) -> str:
    """按相关性对文档列表重新排序。documents 应为 JSON 字符串数组。

    Args:
        query: 查询语句
        documents: JSON 格式的文档字符串数组，如 '["文档1", "文档2"]'
    """
    try:
        doc_list = json.loads(documents)
        if not isinstance(doc_list, list):
            return tool_error("documents 必须是 JSON 字符串数组",
                              cause=ErrorCause.PARAM, retryable=False,
                              hint="请传入形如 '[\"文档1\", \"文档2\"]' 的 JSON 字符串")
    except json.JSONDecodeError:
        doc_list = [d.strip() for d in documents.split("\n") if d.strip()]

    from agent.retrieval.rerank import rerank_documents
    try:
        out = await rerank_documents(query, doc_list)
        out["success"] = True
        out["query"] = query
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="文档重排序")
