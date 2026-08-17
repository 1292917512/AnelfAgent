"""提供者抽象与能力协议。

矩阵模型：Provider（凭据 / 启用状态 / 错误分类）× Capability（统一能力接口）。
能力以 runtime_checkable Protocol 表达——提供者实现哪个方法就拥有哪个能力，
注册表经 isinstance 判定，无第二事实源。新增能力 = 定义 Protocol + 注册表登记；
新增提供者 = 继承 Provider + 按需实现能力方法 + 在 providers/__init__.py 注册。
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Coroutine, Dict, List, Protocol, Tuple, runtime_checkable

from core.log import log
from core.tool_errors import error_from_exception

# 能力标识（注册表 / 配置 / API 的统一词汇）
CAP_SEARCH = "search"  # 检索
CAP_READER = "reader"  # 网页读取
CAP_REPO = "repo"      # 仓库文档

CAPABILITY_LABELS: Dict[str, str] = {
    CAP_SEARCH: "检索",
    CAP_READER: "网页读取",
    CAP_REPO: "仓库文档",
}

# 凭据来源标识（面板展示 / AI 工具诊断用）
SOURCE_CONFIG = "config"  # 提供者所属实体配置文件
SOURCE_LLM = "llm"        # llm_clients.json 供应商凭据回退
SOURCE_ENV = "env"        # 环境变量


@runtime_checkable
class SearchCap(Protocol):
    """检索能力：关键词 → 归一化结果列表。"""

    def search(self, query: str, max_results: int) -> Dict[str, Any]: ...


@runtime_checkable
class ReaderCap(Protocol):
    """网页读取能力：URL → 可读正文（完整内容，分块由工具层统一施加）。

    use_proxy / respect_robots / extract_mode=raw 为直连语义，远程实现可忽略或不支持。
    """

    def read(
        self,
        url: str,
        *,
        timeout: int = 15,
        extract_mode: str = "markdown",
        use_proxy: bool = False,
        respect_robots: bool = False,
    ) -> Dict[str, Any]: ...


@runtime_checkable
class RepoCap(Protocol):
    """仓库文档能力：GitHub 仓库的知识文档检索 / 目录结构 / 文件内容。"""

    def search_doc(self, repo: str, query: str) -> str: ...
    def get_repo_structure(self, repo: str, dir_path: str = "") -> str: ...
    def read_repo_file(self, repo: str, path: str) -> str: ...


# 能力 → 协议（注册表判定与展示的唯一事实源）
CAPABILITY_PROTOCOLS: Dict[str, Any] = {
    CAP_SEARCH: SearchCap,
    CAP_READER: ReaderCap,
    CAP_REPO: RepoCap,
}


class Provider(ABC):
    """提供者抽象：凭据解析、启用状态、错误分类；能力由子类按 Protocol 实现。"""

    name: str = ""
    display_name: str = ""
    description: str = ""
    key_hint: str = ""               # 凭据配置指引（面板 / AI 工具报错提示）
    requires_credential: bool = True  # False 表示开箱可用（如本地直连）

    @abstractmethod
    def credential(self) -> Tuple[str, str]:
        """解析凭据，返回 (api_key, source)；source 为 config/llm/env，未配置返回 ("", "")。"""

    def configured(self) -> bool:
        return not self.requires_credential or bool(self.credential()[0])

    def enabled(self) -> bool:
        """启用状态（web 实体配置，禁用的提供者不参与自动解析）。"""
        from entities.web.web_config import is_enabled
        return is_enabled(self.name)

    @abstractmethod
    def set_api_key(self, api_key: str) -> None:
        """持久化凭据到提供者所属配置文件（空串表示清除）。"""

    def error_response(self, exc: Exception, action: str, hint: str = "") -> str:
        """错误分类为结构化工具错误 JSON（子类按平台错误码覆盖）。"""
        return error_from_exception(exc, action=action, hint=hint or None)


def run_coro_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """同步上下文驱动异步协程（工具工作线程内无运行中的事件循环）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()  # 守卫拒绝时关闭未 awaited 的协程，避免资源告警
    raise RuntimeError("当前处于事件循环内，无法同步驱动异步客户端")


def llm_provider_key(*host_keywords: str) -> Tuple[str, str]:
    """从 llm_clients.json 的 providers 按 base_url 关键字匹配首个带凭据的供应商。

    Returns:
        (api_key, provider_id)，未命中返回 ("", "")
    """
    try:
        from core.path import ConfigPaths
        with open(ConfigPaths.LLM_CLIENTS, encoding="utf-8") as f:
            data = json.load(f)
        for provider in data.get("providers", []):
            base_url = str(provider.get("base_url", "")).lower()
            api_key = str(provider.get("api_key", "")).strip()
            if api_key and any(kw in base_url for kw in host_keywords):
                return api_key, str(provider.get("id", ""))
    except Exception as e:
        log(f"LLM 供应商凭据回退读取失败: {e}", "DEBUG", tag="Web")
    return "", ""


def normalize_references(query: str, items: List[Dict[str, Any]], max_results: int) -> Dict[str, Any]:
    """将提供者原始结果列表归一化为标准结构（title/url/snippet/date?）。"""
    refs: List[Dict[str, Any]] = []
    for item in items[:max_results]:
        ref: Dict[str, Any] = {
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
            "snippet": str(item.get("snippet", "")),
        }
        if item.get("date"):
            ref["date"] = str(item["date"])
        refs.append(ref)
    output: Dict[str, Any] = {"query": query, "sources": len(refs), "references": refs}
    if not refs:
        output["hint"] = "无结果，建议更换关键词重写 query 后重试"
    return output
