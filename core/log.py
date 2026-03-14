"""统一日志接口 - Loguru 版本（缺 loguru 时自动降级为标准库 logging）"""

import os
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Dict, Callable, Optional, Any

try:
    from loguru import logger as _loguru_logger

    _loguru_logger.remove()
    _USE_LOGURU = True
except ImportError:
    import logging as _stdlib_logging

    _loguru_logger = None  # type: ignore[assignment]
    _USE_LOGURU = False

    # 构建 stdlib fallback logger
    _stdlib_handler = _stdlib_logging.StreamHandler(sys.stdout)
    _stdlib_handler.setFormatter(
        _stdlib_logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
    )
    _fallback_logger = _stdlib_logging.getLogger("anelf")
    _fallback_logger.addHandler(_stdlib_handler)
    _fallback_logger.setLevel(_stdlib_logging.DEBUG)

# 向后兼容
logger = _loguru_logger  # type: ignore[assignment]

level_emoji = {"DEBUG": "🔍", "INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}

# 监听器系统
_listeners: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
_all_listeners: List[Callable[[Dict[str, Any]], None]] = []


def format_record(record: dict) -> str:
    """格式化日志记录"""
    emoji = level_emoji.get(record["level"].name, "📝")
    time_str = record["time"].strftime("%H:%M:%S")
    level_name = record["level"].name
    message = record["message"]
    return f"[{time_str}] {emoji} {level_name}: {message}\n"


def _notify_listeners(level: str, message: str, tag: Optional[str] = None):
    """通知监听器"""
    log_data = {"level": level, "message": message, "tag": tag, "timestamp": time.time()}

    # 通知所有监听器（无标签过滤）
    for listener in _all_listeners:
        try:
            listener(log_data)
        except Exception:
            pass  # 监听器错误不应影响主程序

    # 通知特定标签的监听器
    if tag and tag in _listeners:
        for listener in _listeners[tag]:
            try:
                listener(log_data)
            except Exception:
                pass


def log(message: str, level: str = "INFO", tag: Optional[str] = None):
    """
    统一日志函数
    
    Args:
        message: 日志消息
        level: 日志级别
        tag: 标签，用于监听器过滤
    """
    if level in ["ERROR", "CRITICAL"] and sys.exc_info()[0] is not None:
        traceback.print_exc()

    if _USE_LOGURU:
        safe = message.replace("{", "{{").replace("}", "}}").replace("<", r"\<")
        getattr(logger.opt(depth=1), level.lower())(safe)
    else:
        import logging as _logging
        _level_map = {"DEBUG": _logging.DEBUG, "INFO": _logging.INFO, "WARNING": _logging.WARNING,
                      "ERROR": _logging.ERROR, "CRITICAL": _logging.CRITICAL}
        _fallback_logger.log(_level_map.get(level.upper(), _logging.INFO), message)
    _notify_listeners(level, message, tag)


def add_listener(callback: Callable[[Dict[str, Any]], None], tag: Optional[str] = None):
    """
    添加日志监听器
    
    Args:
        callback: 回调函数，接收日志数据字典
        tag: 标签，None 表示监听所有日志
    """
    if tag is None:
        _all_listeners.append(callback)
    else:
        if tag not in _listeners:
            _listeners[tag] = []
        _listeners[tag].append(callback)


def remove_listener(callback: Callable[[Dict[str, Any]], None], tag: Optional[str] = None):
    """
    移除日志监听器
    
    Args:
        callback: 要移除的回调函数
        tag: 标签，None 表示从全局监听器中移除
    """
    if tag is None:
        if callback in _all_listeners:
            _all_listeners.remove(callback)
    else:
        if tag in _listeners and callback in _listeners[tag]:
            _listeners[tag].remove(callback)
            # 清理空列表
            if not _listeners[tag]:
                del _listeners[tag]


def set_log_level(level: str):
    """设置日志等级"""
    if _USE_LOGURU:
        logger.add(sys.stdout, format=format_record, level=level.upper())
    else:
        import logging as _logging
        _level_map = {"DEBUG": _logging.DEBUG, "INFO": _logging.INFO, "WARNING": _logging.WARNING,
                      "ERROR": _logging.ERROR, "CRITICAL": _logging.CRITICAL}
        _fallback_logger.setLevel(_level_map.get(level.upper(), _logging.INFO))


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


def _file_format(record: dict) -> str:
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
def debug(message: str, tag: Optional[str] = None):
    """调试日志"""
    log(message, "DEBUG", tag)


def info(message: str, tag: Optional[str] = None):
    """信息日志"""
    log(message, "INFO", tag)


def warning(message: str, tag: Optional[str] = None):
    """警告日志"""
    log(message, "WARNING", tag)


def error(message: str, tag: Optional[str] = None):
    """错误日志"""
    log(message, "ERROR", tag)


def critical(message: str, tag: Optional[str] = None):
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
