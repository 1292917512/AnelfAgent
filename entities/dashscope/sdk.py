"""dashscope SDK 懒加载与凭据解析（组件内唯一 SDK 入口）。"""

from __future__ import annotations

from typing import Optional, Tuple

from core.config import ConfigManager
from core.log import log

_LOG_TAG = "百炼"


def resolve_api_key() -> str:
    """API Key 解析：组件配置 → llm_clients dashscope 兼容提供者 → 环境变量。"""
    key = str(ConfigManager.get("dashscope_api_key", "") or "").strip()
    if key:
        return key
    key = _key_from_llm_clients() or ""
    if key:
        return key
    import os

    return os.environ.get("DASHSCOPE_API_KEY", "").strip()


def _key_from_llm_clients() -> Optional[str]:
    """从 llm_clients.json 找 dashscope 兼容提供者的 api_key。"""
    import json
    import os

    from core.path import config_dir

    path = os.path.join(config_dir(), "llm_clients.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    for provider in data.get("providers", []):
        base = str(provider.get("base_url", "") or "")
        key = str(provider.get("api_key", "") or "").strip()
        if key and "dashscope.aliyuncs.com" in base:
            return key
    return None


def import_sdk() -> Tuple[Optional[object], str]:
    """导入并武装 dashscope SDK；返回 (dashscope 模块, 不可用原因)。"""
    try:
        import dashscope
    except ImportError:
        return None, "未安装 dashscope SDK（install_python_packages 安装后可用）"
    key = resolve_api_key()
    if not key:
        return None, "未解析到阿里百炼 API Key（dashscope_api_key / llm_clients / 环境变量均无）"
    dashscope.api_key = key
    return dashscope, ""


def sdk_ready() -> bool:
    sdk, _ = import_sdk()
    return sdk is not None


def unavailable_hint() -> str:
    _, reason = import_sdk()
    return reason


def run_sync(func, *args, **kwargs):
    """SDK 同步调用统一走线程（回调线程→事件循环桥接由调用方负责）。"""
    import asyncio

    return asyncio.to_thread(func, *args, **kwargs)


def thread_to_loop(loop, queue):
    """构造 SDK 回调线程 → asyncio 队列的投递函数。"""
    def _put(item) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            pass  # 事件循环已关闭（会话拆除竞态）：静默丢弃

    return _put


def log_sdk_error(scope: str, message: str) -> None:
    log(f"百炼 {scope} 失败: {message}", "WARNING", tag=_LOG_TAG)
