"""统一日志接口 - Loguru 版本（缺 loguru 时自动降级为标准库 logging）"""

import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, List, Optional

if TYPE_CHECKING:
    # Record 仅在 loguru 的 __init__.pyi 存根中定义（运行时不可导入），故仅类型检查时引入
    from loguru import Record


def _resolve_log_stream() -> Any:
    """根据环境变量选择日志输出流（默认 stdout）。"""
    stream = os.getenv("ANELF_LOG_STREAM", "").strip().lower()
    if stream == "stderr":
        return sys.stderr
    if os.getenv("ANELF_MCP_STDIO", "").strip().lower() in {"1", "true", "yes", "on"}:
        # MCP stdio 协议要求 stdout 仅用于 JSONRPC，日志必须走 stderr。
        return sys.stderr
    return sys.stdout


_DEFAULT_LOG_STREAM = _resolve_log_stream()

try:
    from loguru import logger as _loguru_logger

    _loguru_logger.remove()
    _USE_LOGURU = True
    # remove() 清空了内置 sink，这里记录默认 stream sink 的 id（在 format_record 定义后挂载）
    _stream_sink_id: Optional[int] = None
except ImportError:
    import logging as _stdlib_logging

    _loguru_logger = None  # type: ignore[assignment]
    _USE_LOGURU = False
    _stream_sink_id = None

    # 构建 stdlib fallback logger
    _stdlib_handler = _stdlib_logging.StreamHandler(_DEFAULT_LOG_STREAM)
    _stdlib_handler.setFormatter(
        _stdlib_logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
    )
    _fallback_logger = _stdlib_logging.getLogger("anelf")
    _fallback_logger.addHandler(_stdlib_handler)
    _fallback_logger.setLevel(_stdlib_logging.DEBUG)

logger = _loguru_logger

level_emoji = {"DEBUG": "🔍", "INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}
# 合法日志级别集合
_VALID_LEVELS = frozenset(level_emoji)

# stdlib 降级路径的级别映射（log() 与 set_log_level() 共用）
import logging as _logging

_STDLIB_LEVEL_MAP = {
    "DEBUG": _logging.DEBUG, "INFO": _logging.INFO, "WARNING": _logging.WARNING,
    "ERROR": _logging.ERROR, "CRITICAL": _logging.CRITICAL,
}

# 监听器系统
_listeners: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
_all_listeners: List[Callable[[Dict[str, Any]], None]] = []
_listeners_lock = threading.Lock()


def _internal_debug(message: str) -> None:
    """日志模块内部 DEBUG 输出（直接写底层 logger，避免递归调用 log()）。"""
    if _USE_LOGURU:
        logger.opt(depth=1).debug(message.replace("{", "{{").replace("}", "}}"))
    else:
        _fallback_logger.debug(message)


def _format_record(record: Any) -> str:
    """格式化日志记录"""
    emoji = level_emoji.get(record["level"].name, "📝")
    time_str = record["time"].strftime("%H:%M:%S")
    level_name = record["level"].name
    message = record["message"]
    return f"[{time_str}] {emoji} {level_name}: {message}\n"


def _coerce_utf8_stream(stream: Any) -> Any:
    """为非 UTF-8 stdout/stderr 加 utf-8 编码包装，避免中文/非 ASCII 写日志时触发 UnicodeEncodeError。

    loguru 会把字符串经 sink 写回原 stream；若 stream 编码是 ASCII，抛 'ascii' codec 错并冒泡。
    这里用 errors='replace' 兜底（极端情况无法用 utf-8 时用 ? 替代），确保日志写不出去也不影响业务。
    """
    enc = getattr(stream, "encoding", None)
    if not enc or enc.lower().replace("-", "").startswith("utf"):
        return stream
    try:
        import io
        raw = getattr(stream, "buffer", None) or getattr(stream, "fileno", lambda: None)()
        if raw is None:
            return stream
        return io.TextIOWrapper(raw, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        return stream


def _emit_loguru(level: str, message: str, with_exc: bool) -> None:
    """向 loguru 写入日志，自身异常被吞掉（绝不影响业务调用）。"""
    if not _USE_LOGURU:
        return
    try:
        safe = message.replace("{", "{{").replace("}", "}}").replace("<", r"\<")
        getattr(_loguru_logger.opt(depth=1, exception=with_exc), level.lower())(safe)
    except Exception:
        # 日志系统故障不能影响业务调用，最末也避免被 fallback 链捕获
        try:
            sys.stderr.write(f"[log-error] {level}: {message}\n")
        except Exception:
            pass


# 挂载默认 stream sink：非 launch.py 入口（未调用 set_log_level）也能正常输出日志，
# 避免 remove() 清空内置 sink 后日志全部丢失。
# 强制 UTF-8 编码并降级 errors='replace'，防止 stdout 默认编码为 ASCII（容器/CI/POSIX LANG=C）
# 时中文/非 ASCII 内容触发 UnicodeEncodeError，避免日志自身的异常冒泡影响业务调用。
if _USE_LOGURU:
    _log_stream = _coerce_utf8_stream(_DEFAULT_LOG_STREAM)
    # enqueue=True：日志经队列线程异步写出，高频路径（工具调用/LLM 调用/delta 事件）
    # 不再在事件循环线程同步 write stdout
    _stream_sink_id = logger.add(_log_stream, format=_format_record, level="DEBUG", enqueue=True)


def _notify_listeners(level: str, message: str, tag: Optional[str] = None) -> None:
    """通知监听器"""
    log_data = {"level": level, "message": message, "tag": tag, "timestamp": time.time()}

    with _listeners_lock:
        all_listeners = list(_all_listeners)
        tag_listeners = list(_listeners.get(tag, [])) if tag else []

    # 通知所有监听器（无标签过滤）与特定标签的监听器；
    # 监听器异常记 DEBUG，不影响主程序
    for listener in all_listeners + tag_listeners:
        try:
            listener(log_data)
        except Exception as e:
            _internal_debug(f"日志监听器异常: {type(e).__name__}: {e}")


def log(message: str, level: str = "INFO", tag: Optional[str] = None) -> None:
    """
    统一日志函数

    Args:
        message: 日志消息
        level: 日志级别（非法级别回退 INFO 并记 DEBUG）
        tag: 标签，用于监听器过滤
    """
    level = level.upper()
    if level not in _VALID_LEVELS:
        _internal_debug(f"非法日志级别 {level!r}，已回退为 INFO")
        level = "INFO"

    message = _maybe_sanitize(message)
    with_exc = level in ["ERROR", "CRITICAL"] and sys.exc_info()[0] is not None

    if _USE_LOGURU:
        _emit_loguru(level, message, with_exc)
    else:
        try:
            _fallback_logger.log(
                _STDLIB_LEVEL_MAP.get(level, _logging.INFO), message, exc_info=with_exc,
            )
        except Exception:
            # 兜底：终端编码异常时 stdlib 也会抛 UnicodeEncodeError，绝不让日志炸业务
            try:
                sys.stderr.write(f"[log-fallback-error] {level}: {message}\n")
            except Exception:
                pass
    _notify_listeners(level, message, tag)


def _maybe_sanitize(message: str) -> str:
    """日志消息脱敏（sanitize_text 内部含快速预检，无敏感特征时零正则开销）。"""
    try:
        from core.sanitizer import is_sanitize_enabled, sanitize_text
        if not is_sanitize_enabled():
            return message
        return sanitize_text(message)
    except Exception:
        return message


def add_listener(callback: Callable[[Dict[str, Any]], None], tag: Optional[str] = None) -> None:
    """
    添加日志监听器

    Args:
        callback: 回调函数，接收日志数据字典
        tag: 标签，None 表示监听所有日志
    """
    with _listeners_lock:
        if tag is None:
            _all_listeners.append(callback)
        else:
            _listeners.setdefault(tag, []).append(callback)


def remove_listener(callback: Callable[[Dict[str, Any]], None], tag: Optional[str] = None) -> None:
    """
    移除日志监听器

    Args:
        callback: 要移除的回调函数
        tag: 标签，None 表示从全局监听器中移除
    """
    with _listeners_lock:
        if tag is None:
            if callback in _all_listeners:
                _all_listeners.remove(callback)
        else:
            if tag in _listeners and callback in _listeners[tag]:
                _listeners[tag].remove(callback)
                # 清理空列表
                if not _listeners[tag]:
                    del _listeners[tag]


def set_log_level(level: str) -> None:
    """设置日志等级"""
    global _stream_sink_id
    if _USE_LOGURU:
        # 重设等级前先移除旧 stream sink，避免重复 sink 导致日志双写
        if _stream_sink_id is not None:
            logger.remove(_stream_sink_id)
        _stream_sink_id = logger.add(_coerce_utf8_stream(_DEFAULT_LOG_STREAM), format=_format_record, level=level.upper(), enqueue=True)
    else:
        _fallback_logger.setLevel(_STDLIB_LEVEL_MAP.get(level.upper(), _logging.INFO))


# ======================================================================
# 文件日志持久化
# ======================================================================

_LOG_CONFIGS = {
    "日志": {
        "log_file_enabled": {
            "default": False,
            "description": "将日志持久化输出到文件（logs/anelf.log）",
        },
    }
}

_FILE_PATH = "logs/anelf.log"
_file_sink_id: Optional[int] = None


def _file_format(record: "Record") -> str:
    """文件日志格式（不含 emoji，便于检索）"""
    time_str = record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    level_name = f"{record['level'].name:<8}"
    return f"[{time_str}] {level_name} | {record['message']}\n"


def enable_file_logging() -> bool:
    """根据配置启用文件日志持久化，需在 ConfigManager 初始化后调用。"""
    global _file_sink_id

    try:
        from core.config import ConfigManager, register_configs
        register_configs(_LOG_CONFIGS)

        if not ConfigManager.get("log_file_enabled", False):
            return False

        os.makedirs(os.path.dirname(os.path.abspath(_FILE_PATH)) or ".", exist_ok=True)

        if _USE_LOGURU:
            if _file_sink_id is not None:
                logger.remove(_file_sink_id)

            _file_sink_id = logger.add(
                _FILE_PATH,
                format=_file_format,
                level="DEBUG",
                rotation="10 MB",
                retention="7 days",
                compression="zip",
                encoding="utf-8",
                enqueue=True,
            )
        else:
            import logging as _logging
            from logging.handlers import RotatingFileHandler
            handler = RotatingFileHandler(
                _FILE_PATH, maxBytes=10 * 1024 * 1024, backupCount=7,
                encoding="utf-8",
            )
            handler.setFormatter(
                _logging.Formatter("[%(asctime)s] %(levelname)-8s | %(message)s",
                                   datefmt="%Y-%m-%d %H:%M:%S")
            )
            handler.setLevel(_logging.DEBUG)
            _fallback_logger.addHandler(handler)

        log(f"文件日志已启用: {_FILE_PATH}")
        return True

    except Exception as e:
        log(f"启用文件日志失败: {e}", "ERROR")
        return False


# 便捷函数
def debug(message: str, tag: Optional[str] = None) -> None:
    """调试日志"""
    log(message, "DEBUG", tag)


def info(message: str, tag: Optional[str] = None) -> None:
    """信息日志"""
    log(message, "INFO", tag)


def warning(message: str, tag: Optional[str] = None) -> None:
    """警告日志"""
    log(message, "WARNING", tag)


def error(message: str, tag: Optional[str] = None) -> None:
    """错误日志"""
    log(message, "ERROR", tag)


def critical(message: str, tag: Optional[str] = None) -> None:
    """严重错误日志"""
    log(message, "CRITICAL", tag)


# ======================================================================
# 内存日志缓冲区 — 供 AI 工具查询近期日志
# ======================================================================

_LOG_BUFFER_MAX = 2000

@dataclass
class LogRecord:
    """一条日志记录。"""
    level: str
    message: str
    tag: Optional[str]
    timestamp: float

_log_buffer: Deque[LogRecord] = deque(maxlen=_LOG_BUFFER_MAX)


def _buffer_listener(data: Dict[str, Any]) -> None:
    """日志监听器：将日志写入环形缓冲区。"""
    _log_buffer.append(LogRecord(
        level=data.get("level", "INFO"),
        message=data.get("message", ""),
        tag=data.get("tag"),
        timestamp=data.get("timestamp", time.time()),
    ))

# 自动注册缓冲区监听器
add_listener(_buffer_listener)


def query_log_buffer(
    *,
    level: Optional[str] = None,
    tag: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """查询内存日志缓冲区。

    Args:
        level: 过滤日志级别（INFO/WARNING/ERROR 等）
        tag: 过滤标签
        keyword: 关键词搜索（匹配消息内容）
        limit: 返回条数上限
    """
    results: List[Dict[str, Any]] = []
    for record in reversed(_log_buffer):
        if level and record.level != level.upper():
            continue
        if tag and record.tag != tag:
            continue
        if keyword and keyword.lower() not in record.message.lower():
            continue
        results.append({
            "level": record.level,
            "message": record.message,
            "tag": record.tag or "",
            "time": time.strftime("%H:%M:%S", time.localtime(record.timestamp)),
        })
        if len(results) >= limit:
            break
    results.reverse()
    return results


def clear_log_buffer() -> int:
    """清空内存日志缓冲区，返回清除的条数。"""
    count = len(_log_buffer)
    _log_buffer.clear()
    return count


def get_log_buffer_stats() -> Dict[str, Any]:
    """获取日志缓冲区统计信息。"""
    level_counts: Dict[str, int] = {}
    tag_counts: Dict[str, int] = {}
    for record in _log_buffer:
        level_counts[record.level] = level_counts.get(record.level, 0) + 1
        if record.tag:
            tag_counts[record.tag] = tag_counts.get(record.tag, 0) + 1
    return {
        "total": len(_log_buffer),
        "capacity": _LOG_BUFFER_MAX,
        "by_level": level_counts,
        "by_tag": tag_counts,
    }
