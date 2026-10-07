"""长任务结构化交接 — 跨运行的有界 handoff。

问题：心跳任务每次运行都是全新上下文（reflect 隔离），轮间信息靠记忆库
recall 检索——概率性召回对"连续 N 天整理技能库"这类长任务不够确定。
handoff 提供**确定性接力**：运行结束时从输出提取 ``# HANDOFF`` 块持久化，
下次运行注入任务指令（工作区文件仍是第一等的长期记忆，handoff 只携带
"进度与下一步"的浓缩状态）。

格式（宽容解析）：
    输出末尾一段以 ``# HANDOFF`` 行起始的块；块体优先按 JSON 解析
    （{"summary", "next_steps", "blocker"}），失败时按纯文本整段保留。
    提取后的干净输出继续走原有任务流程（存记忆/过滤），handoff 单独落盘。

失败容错：解析失败保留旧 handoff + 日志，绝不阻断任务执行与 at-least-once。

Model Experience：handoff 注入进任务指令（[系统任务] 消息的 user 内容），
仅 reflect 临时上下文可见，不触碰对话缓存前缀；token 影响上限
``task_handoff_max_chars``（默认 4000）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

from core.config import get_config_int
from core.file_utils import atomic_write_text
from core.log import log
from core.path import ConfigPaths

# "# HANDOFF" 起始行（允许尾随任意字符，如 "# HANDOFF:"）
_HANDOFF_RE = re.compile(r"^[ \t]*#[ \t]*HANDOFF\b[^\n]*\n?", re.IGNORECASE | re.MULTILINE)


def _safe_stem(task_name: str) -> str:
    # 任务名即文件名安全化（TaskDefinition.name 限于安全字符，仍做防御）
    return re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff-]", "_", task_name)[:80] or "_"


def _handoff_path(task_name: str) -> Path:
    return Path(ConfigPaths.TASKS_DIR) / f"{_safe_stem(task_name)}.handoff.json"


def _max_chars() -> int:
    return max(200, get_config_int("task_handoff_max_chars", 4000))


def load_handoff(task_name: str) -> str:
    """读取上次运行留下的交接文本（无则空串）。"""
    try:
        import os
        path = _handoff_path(task_name)
        if not os.path.isfile(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        text = str(data.get("handoff", "") or "")
        return text[:_max_chars()]
    except Exception:
        return ""


def save_handoff(task_name: str, text: str) -> bool:
    """持久化交接文本（截断至上限，原子写）。返回是否成功。"""
    text = (text or "").strip()[:_max_chars()]
    if not text:
        return False
    try:
        import time
        atomic_write_text(
            _handoff_path(task_name),
            json.dumps({"handoff": text, "updated_at": time.time()},
                       ensure_ascii=False, indent=2),
        )
        return True
    except Exception as exc:
        log(f"handoff 保存失败（保留旧值）: {task_name} {exc}", "WARNING", tag="任务")
        return False


def delete_handoff(task_name: str) -> bool:
    """删除任务的交接文件（任务删除时随历史一并清理）。返回是否有文件被删。"""
    try:
        path = _handoff_path(task_name)
        if path.is_file():
            path.unlink()
            return True
    except Exception as exc:
        log(f"handoff 清理失败: {task_name} {exc}", "WARNING", tag="任务")
    return False


def prune_orphans(valid_names: Set[str]) -> List[str]:
    """对账清除无对应任务文件的交接文件，返回被清除的安全名（无残留返回空）。

    与 history.prune_orphans 同节奏（心跳引擎构造/reload 对账调用）。
    valid_names 须来自注册表正常枚举——空集语义是"全部清理"，
    调用方负责在目录不可枚举时跳过（防误清）。
    """
    pruned: List[str] = []
    try:
        tasks_dir = Path(ConfigPaths.TASKS_DIR)
        if not tasks_dir.is_dir():
            return []
        alive_stems = {_safe_stem(name) for name in valid_names}
        for file in sorted(tasks_dir.glob("*.handoff.json")):
            stem = file.name[: -len(".handoff.json")]
            if stem in alive_stems:
                continue
            try:
                file.unlink()
                pruned.append(stem)
            except OSError as exc:
                log(f"孤儿 handoff 清理失败 [{file.name}]: {exc}", "WARNING", tag="任务")
    except Exception as exc:
        log(f"handoff 对账清理失败: {exc}", "WARNING", tag="任务")
    return pruned


def extract_handoff(output: str) -> Tuple[str, Optional[str]]:
    """从任务输出中分离 (干净输出, handoff 文本或 None)。

    提取最后一个 "# HANDOFF" 行之后的全部内容作为交接块（约定写在输出
    末尾）；干净输出为该行之前的部分（去尾部空白）。
    """
    if not output:
        return output, None
    matches = list(_HANDOFF_RE.finditer(output))
    if not matches:
        return output, None
    last = matches[-1]
    clean = output[:last.start()].rstrip()
    raw_block = output[last.end():].strip()
    if not raw_block:
        return clean, None
    # JSON 优先（结构化），失败按纯文本宽容保留
    try:
        parsed = json.loads(raw_block)
        if isinstance(parsed, dict):
            # 归一化为可读文本（下次注入直接用）
            parts = []
            if parsed.get("summary"):
                parts.append(f"摘要: {parsed['summary']}")
            if parsed.get("next_steps"):
                steps = parsed["next_steps"]
                if isinstance(steps, list):
                    parts.append("下一步:\n" + "\n".join(f"- {s}" for s in steps))
                else:
                    parts.append(f"下一步: {steps}")
            if parsed.get("blocker"):
                parts.append(f"阻塞: {parsed['blocker']}")
            handoff = "\n".join(parts) or raw_block
        else:
            handoff = raw_block
    except (json.JSONDecodeError, TypeError):
        handoff = raw_block
    return clean, handoff or None


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_HANDOFF_CONFIGS = {
    "task/handoff": {
        "task_handoff_max_chars": {
            "description": "长任务结构化交接（# HANDOFF 块）的持久化字符上限",
            "default": 4000,
            "advanced": True,
            "unit": "字符",
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_HANDOFF_CONFIGS)
