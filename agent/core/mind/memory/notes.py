"""便签记忆 -- Markdown 文件形式的持久化笔记。

支持 MEMORY.md（常青知识）和 memory/ 目录下的多文件管理。
Agent 每次对话时自动读取注入到上下文，并可通过工具随时编辑更新。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from core.log import log
from entities._sdk import deferred_tool, activate_group
from .memory_utils import list_workspace_md_files

_workspace_dir: Optional[Path] = None
_file_lock = asyncio.Lock()


def _repo_root() -> Path:
    """定位项目根目录（notes.py -> memory/ -> mind/ -> core/ -> agent/ -> 项目根）。"""
    return Path(__file__).resolve().parents[4]


def get_workspace_dir() -> Path:
    """获取记忆工作区目录。"""
    return _workspace_dir or (_repo_root() / "config")


def get_notes_path() -> Path:
    """获取主便签文件路径（memory/MEMORY.md）。"""
    return get_memory_dir() / "MEMORY.md"


def get_memory_dir() -> Path:
    """获取记忆文件目录（workspace/memory/）。"""
    return get_workspace_dir() / "memory"


def load_notes_content() -> str:
    """读取主便签文件内容，文件不存在返回空字符串。"""
    p = get_notes_path()
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def _atomic_write(target: Path, content: str) -> None:
    """原子写入文件：先写临时文件，再 os.replace 避免并发写入导致数据损坏。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), suffix=".tmp", prefix=".notes_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _schedule_file_resync(target)


def _schedule_file_resync(file_path: Path) -> None:
    """MD 文件写入后异步触发该文件的 chunks 增量重新索引。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_resync_single_file(file_path))


async def _resync_single_file(file_path: Path) -> None:
    """对单个 MD 文件重新生成 chunks 索引。"""
    try:
        from services._runtime import require_runtime
        rt = require_runtime()
        store = rt.mind.memory_store
        embedder = rt.mind.embedder
        if not store:
            return
        ws = get_workspace_dir()
        from agent.core.mind.memory.memory_sync import sync_files
        await sync_files(store, embedder, ws)
    except Exception as e:
        from core.log import log as _log
        _log(f"文件索引增量同步失败: {e}", "DEBUG", tag="思维")


def _with_line_numbers(content: str, start: int = 1) -> str:
    """为文本内容每行添加行号前缀，格式为 `{N} | {line}`。"""
    lines = content.splitlines()
    end = start + len(lines) - 1
    width = max(len(str(end)), 1)
    return "\n".join(f"{start + i:{width}d} | {line}" for i, line in enumerate(lines))


def list_all_memory_files() -> list[Dict[str, str]]:
    """列出所有 MD 便签文件及其大小。"""
    ws = get_workspace_dir()
    paths = list_workspace_md_files(ws)
    files: list[Dict[str, str]] = []
    for p in paths:
        rel = str(p.relative_to(ws)).replace("\\", "/")
        try:
            content = p.read_text(encoding="utf-8")
            files.append({
                "path": rel,
                "size": str(p.stat().st_size),
                "lines": str(content.count("\n") + 1),
            })
        except Exception:
            files.append({"path": rel, "size": "0", "lines": "0"})
    return files


def _validate_md_path(file_path: str) -> Path:
    """验证文件路径在工作区内且为 .md 文件，返回解析后的绝对路径。"""
    ws = get_workspace_dir()
    target = (ws / file_path).resolve()
    if not str(target).startswith(str(ws.resolve())):
        raise ValueError("路径不在工作区内")
    if target.suffix != ".md":
        raise ValueError("只允许操作 .md 文件")
    return target


def delete_memory_file(file_path: str) -> bool:
    """删除指定 MD 便签文件，返回是否成功。不允许删除 data/ 目录下的数据库文件。"""
    target = _validate_md_path(file_path)
    if "data" in target.parts:
        raise ValueError("不允许删除 data/ 目录下的文件")
    if not target.exists():
        return False
    target.unlink()
    return True


def read_memory_file(file_path: str) -> str:
    """读取指定记忆文件的内容。"""
    target = _validate_md_path(file_path)
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8")


def write_memory_file(file_path: str, content: str) -> int:
    """写入指定记忆文件（原子写入），返回行数。"""
    target = _validate_md_path(file_path)
    _atomic_write(target, content)
    return content.count("\n") + 1


def append_to_memory_file(file_path: str, content: str) -> int:
    """在指定记忆文件末尾追加内容，返回追加后的总行数。文件不存在时自动创建。"""
    target = _validate_md_path(file_path)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    new_content = existing + content
    _atomic_write(target, new_content)
    return new_content.count("\n") + 1


def patch_memory_file_content(
    file_path: str, old_text: str, new_text: str, replace_all: bool = False
) -> Dict[str, int]:
    """在记忆文件中进行字符串查找替换。

    返回包含 replaced（实际替换次数）和 total_occurrences（原始匹配总数）的字典。
    old_text 不存在时抛出 ValueError。
    """
    target = _validate_md_path(file_path)
    if not target.exists():
        raise FileNotFoundError(f"{file_path} 不存在")
    content = target.read_text(encoding="utf-8")
    total = content.count(old_text)
    if total == 0:
        raise ValueError(f"未找到目标文本，替换失败")
    if replace_all:
        new_content = content.replace(old_text, new_text)
        replaced = total
    else:
        new_content = content.replace(old_text, new_text, 1)
        replaced = 1
    _atomic_write(target, new_content)
    return {"replaced": replaced, "total_occurrences": total}


def edit_file_lines(
    file_path: str, start_line: int, end_line: int, new_content: str
) -> Dict[str, int]:
    """替换或插入记忆文件中指定行范围（1-indexed，闭区间）的内容。

    start_line = end_line + 1 时为纯插入模式（在 end_line 行后插入，不替换任何行）。
    new_content 为空字符串时删除该行范围。end_line 为 -1 时表示文件最后一行。
    返回包含 total_lines（修改后总行数）的字典。
    """
    target = _validate_md_path(file_path)
    if not target.exists():
        raise FileNotFoundError(f"{file_path} 不存在")
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    total = len(lines)
    if end_line < 0:
        end_line = total
    start_line = max(1, start_line)
    end_line = min(end_line, total)
    if start_line > end_line + 1:
        raise ValueError(f"start_line ({start_line}) 超出有效范围（end_line={end_line}）")
    replacement: list[str] = []
    if new_content:
        if not new_content.endswith("\n"):
            new_content += "\n"
        replacement = new_content.splitlines(keepends=True)
    new_lines = lines[: start_line - 1] + replacement + lines[end_line:]
    _atomic_write(target, "".join(new_lines))
    return {"total_lines": len(new_lines)}


# ------------------------------------------------------------------
# 段落级操作（Markdown 标题锚定）
# ------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s")


def _parse_sections(content: str) -> list[dict]:
    """解析 Markdown 标题结构。

    返回每个标题段落的 dict：heading（标题全文）、level（层级 1-6）、
    heading_line（1-indexed）、end_line（段落末行，1-indexed）、content_lines（含标题行数）。
    段落范围从标题行到下一个同级或更高级标题前一行（或文件末尾）。
    """
    lines = content.splitlines()
    total = len(lines)
    sections: list[dict] = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            sections.append({
                "heading": line.strip(),
                "level": len(m.group(1)),
                "heading_line": i + 1,
                "end_line": total,
            })
    for i, sec in enumerate(sections):
        for j in range(i + 1, len(sections)):
            if sections[j]["level"] <= sec["level"]:
                sec["end_line"] = sections[j]["heading_line"] - 1
                break
        sec["content_lines"] = sec["end_line"] - sec["heading_line"] + 1
    return sections


def _find_section(
    sections: list[dict], heading: str
) -> tuple[Optional[dict], int]:
    """在段落列表中查找匹配标题，返回 (首个匹配的段落 info, 匹配总数)。"""
    matches = [s for s in sections if s["heading"] == heading]
    return (matches[0] if matches else None, len(matches))


def view_file_outline(file_path: str) -> dict:
    """获取记忆文件的 Markdown 标题大纲（仅标题行，不含正文）。"""
    target = _validate_md_path(file_path)
    if not target.exists():
        raise FileNotFoundError(f"{file_path} 不存在")
    content = target.read_text(encoding="utf-8")
    sections = _parse_sections(content)
    total_lines = content.count("\n") + 1 if content else 0
    return {
        "total_lines": total_lines,
        "outline": [
            {
                "heading": s["heading"],
                "level": s["level"],
                "line": s["heading_line"],
                "content_lines": s["content_lines"],
            }
            for s in sections
        ],
    }


def read_section_content(file_path: str, heading: str) -> dict:
    """按标题读取段落。返回 body（不含标题行）和带行号的 view（含标题行）。"""
    target = _validate_md_path(file_path)
    if not target.exists():
        raise FileNotFoundError(f"{file_path} 不存在")
    file_content = target.read_text(encoding="utf-8")
    sections = _parse_sections(file_content)
    sec, count = _find_section(sections, heading)
    if sec is None:
        raise ValueError(f"未找到段落 '{heading}'")
    all_lines = file_content.splitlines()
    heading_line: int = sec["heading_line"]
    end_line: int = sec["end_line"]
    body = "\n".join(all_lines[heading_line:end_line])
    section_text = "\n".join(all_lines[heading_line - 1:end_line])
    view = _with_line_numbers(section_text, start=heading_line)
    result: dict = {
        "heading": heading,
        "start_line": heading_line,
        "end_line": end_line,
        "content": body,
        "view": view,
    }
    if count > 1:
        result["hint"] = f"存在 {count} 个同名段落，当前为第 1 个"
    return result


def write_section_content(
    file_path: str, heading: str, content: str, after: str = ""
) -> dict:
    """替换或创建指定标题段落的内容。

    heading 存在时替换其 body（保留标题行），after 参数被忽略。
    heading 不存在时创建新段落；after 指定插入位置锚点，为空则追加到文件末尾。
    """
    target = _validate_md_path(file_path)
    if not target.exists():
        raise FileNotFoundError(f"{file_path} 不存在")
    file_content = target.read_text(encoding="utf-8")
    sections = _parse_sections(file_content)
    lines = file_content.splitlines(keepends=True)
    sec, count = _find_section(sections, heading)
    if sec is not None:
        heading_line: int = sec["heading_line"]
        end_line: int = sec["end_line"]
        body_lines: list[str] = []
        if content:
            if not content.endswith("\n"):
                content += "\n"
            body_lines = content.splitlines(keepends=True)
        new_lines = lines[:heading_line] + body_lines + lines[end_line:]
        _atomic_write(target, "".join(new_lines))
        return {
            "action": "replaced",
            "heading": heading,
            "total_lines": len(new_lines),
            "duplicates": count - 1,
        }
    if content:
        if not content.endswith("\n"):
            content += "\n"
        new_block = f"{heading}\n{content}"
    else:
        new_block = f"{heading}\n"
    new_block_lines = new_block.splitlines(keepends=True)
    if after:
        anchor, _ = _find_section(sections, after)
        if anchor is None:
            raise ValueError(f"after 锚点段落 '{after}' 未找到")
        insert_pos: int = anchor["end_line"]
        if lines and insert_pos <= len(lines) and not lines[insert_pos - 1].endswith("\n"):
            lines[insert_pos - 1] += "\n"
        new_lines = lines[:insert_pos] + ["\n"] + new_block_lines + lines[insert_pos:]
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        new_lines = lines + ["\n"] + new_block_lines
    _atomic_write(target, "".join(new_lines))
    return {"action": "created", "heading": heading, "total_lines": len(new_lines)}


def delete_section_content(file_path: str, heading: str) -> dict:
    """删除指定标题段落（含标题行和全部内容）。"""
    target = _validate_md_path(file_path)
    if not target.exists():
        raise FileNotFoundError(f"{file_path} 不存在")
    file_content = target.read_text(encoding="utf-8")
    sections = _parse_sections(file_content)
    sec, count = _find_section(sections, heading)
    if sec is None:
        raise ValueError(f"未找到段落 '{heading}'")
    lines = file_content.splitlines(keepends=True)
    heading_line: int = sec["heading_line"]
    end_line: int = sec["end_line"]
    new_lines = lines[:heading_line - 1] + lines[end_line:]
    _atomic_write(target, "".join(new_lines))
    result: dict = {
        "deleted_lines": end_line - heading_line + 1,
        "total_lines": len(new_lines),
    }
    if count > 1:
        result["hint"] = f"还有 {count - 1} 个同名段落未删除"
    return result


def consolidate_heartbeat(max_entries: Optional[int] = None) -> int:
    """整理 heartbeat.md，保留最近 max_entries 条记录。返回裁剪的条目数。"""
    if max_entries is None:
        try:
            from agent.core.config import get_config_provider
            max_entries = get_config_provider().mind.heartbeat_max_entries
        except Exception:
            max_entries = 50

    heartbeat_path = get_memory_dir() / "heartbeat.md"
    if not heartbeat_path.exists():
        return 0

    text = heartbeat_path.read_text(encoding="utf-8")
    blocks = text.split("\n### ")
    if len(blocks) <= max_entries + 1:
        return 0

    header = blocks[0]
    kept = blocks[-max_entries:]
    trimmed = len(blocks) - 1 - max_entries
    new_text = header + "\n### " + "\n### ".join(kept)
    _atomic_write(heartbeat_path, new_text)
    log(f"heartbeat.md 整理: 裁剪 {trimmed} 条旧记录", tag="思维")
    return trimmed


_NOTES_EMPTY_HINT = (
    "[个人笔记/便签记忆]\n"
    "你的便签记忆目前为空。\n"
    "便签是你跨对话持久保存信息的笔记本，用于记录计划和关键记忆。\n"
    "当你在对话中获知重要信息时，请主动使用 write_notes 工具记录，例如：\n"
    "  - 主人的身份、喜好、习惯\n"
    "  - 待办计划、任务进度、阶段性目标\n"
    "  - 重要事件、里程碑、经验总结\n"
    "便签采用 Markdown 格式，建议用 ## 标题分类归档。\n"
    "也可以使用 write_memory_file 将内容写入 memory/ 目录下的分类文件中。"
)


def _build_file_index() -> str:
    """生成其他 MD 便签文件的索引摘要，让 AI 知道有哪些记忆文件可查阅。"""
    try:
        files = list_all_memory_files()
    except Exception:
        return ""
    if not files:
        return ""
    lines = ["[可用便签文件] 使用 view_memory_outline 查看结构，read_section 读取章节"]
    for f in files:
        path = f.get("path", "")
        if path.endswith("MEMORY.md") or path.endswith("heartbeat.md"):
            continue
        line_count = f.get("lines", "?")
        lines.append(f"  - {path} ({line_count} 行)")
    return "\n".join(lines) if len(lines) > 1 else ""


def build_notes_system_message() -> List[dict]:
    """将便签内容构建为 system 消息列表。空便签时注入记录引导。"""
    content = load_notes_content()
    if not content.strip():
        return [{"role": "system", "content": _NOTES_EMPTY_HINT}]
    file_index = _build_file_index()
    if file_index:
        content = f"{content}\n\n{file_index}"
    return [{"role": "system", "content": f"[个人笔记/便签记忆]\n{content}"}]


def register_notes_tools(workspace_dir: Optional[Path] = None) -> None:
    """注入工作区路径并批量注册便签记忆工具。"""
    global _workspace_dir
    if workspace_dir:
        _workspace_dir = workspace_dir
    count = activate_group("notes", "便签记忆 - 计划与关键记忆的持久化笔记本（支持多文件）")
    log(f"便签记忆工具已注册 ({count} 个) -> {get_workspace_dir()}", tag="思维")


# ------------------------------------------------------------------
# 工具实现（带异步锁保护写入）
# ------------------------------------------------------------------

@deferred_tool(
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "读取主便签记忆（memory/MEMORY.md）的全部内容。"
        "返回两个字段：content（原始内容，用于 write_notes/patch_memory_file 的写回）"
        "和 view（带行号的显示内容，用于 edit_memory_lines 定位行号）。"
        "对文件做精确修改时，old_text 和写回内容必须来自 content 字段，不要包含 view 中的行号前缀。"
    ),
)
async def read_notes() -> str:
    """读取主便签记忆的全部内容。"""
    try:
        content = load_notes_content()
        if not content:
            return json.dumps(
                {"content": "", "message": "便签为空，还没有记录任何内容"},
                ensure_ascii=False,
            )
        return json.dumps(
            {"content": content, "view": _with_line_numbers(content)},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "覆写主便签记忆（memory/MEMORY.md）的全部内容。"
        "需要整体修改时，先用 read_notes 获取 content 字段（原始内容），在其基础上修改后整体写回。"
        "不要将 view 字段（带行号前缀的内容）写入文件。"
    ),
)
async def write_notes(content: str) -> str:
    """覆写主便签记忆的全部内容。

    Args:
        content: 要写入的完整 Markdown 内容
    """
    try:
        async with _file_lock:
            p = get_notes_path()
            _atomic_write(p, content)
        lines = content.count("\n") + 1
        log(f"便签更新: {lines} 行", tag="思维")
        return json.dumps(
            {"ok": True, "message": f"便签已更新（{lines} 行）"},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "列出所有 Markdown 便签文件（memory/*.md）及其行数。"
        "【注意】这里只列出 MD 便签文件，不包含数据库长期记忆。"
        "数据库长期记忆（语义/事件/实体/反思/永久记忆）请使用 memory 分组的工具："
        "recall（语义搜索）、memory_index（浏览标签）、memory_deep_search（分页查看所有记忆）。"
    ),
)
async def list_memory_files() -> str:
    """列出所有 MD 便签文件（不含数据库记忆）。"""
    try:
        files = list_all_memory_files()
        return json.dumps({
            "note": "以下为 Markdown 便签文件，数据库长期记忆请用 recall/memory_index 工具查询",
            "count": len(files),
            "files": files,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    name="read_memory_file",
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "读取指定记忆文件的内容（memory/reflections.md、memory/entities.md 等）。"
        "返回两个字段：content（原始内容，用于 write_memory_file/patch_memory_file 的写回）"
        "和 view（带行号的显示内容，用于 edit_memory_lines 定位行号）。"
        "对文件做精确修改时，old_text 和写回内容必须来自 content 字段，不要包含 view 中的行号前缀。"
    ),
)
async def _tool_read_memory_file(file_path: str) -> str:
    """读取指定记忆文件的内容。

    Args:
        file_path: 文件路径，如 memory/MEMORY.md、memory/reflections.md
    """
    try:
        content = read_memory_file(file_path)
        if not content:
            return json.dumps({"content": "", "message": f"{file_path} 为空或不存在"}, ensure_ascii=False)
        return json.dumps(
            {"path": file_path, "content": content, "view": _with_line_numbers(content)},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    name="write_memory_file",
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "写入或编辑 MD 便签文件（完整覆写）。文件不存在时自动创建。"
        "需要整体修改时，先用 read_memory_file 获取 content 字段（原始内容），在其基础上修改后写回。"
        "不要将 view 字段（带行号前缀的内容）写入文件。"
    ),
)
async def _tool_write_memory_file(file_path: str, content: str) -> str:
    """写入或编辑 MD 便签文件（完整覆写）。

    Args:
        file_path: 文件路径，如 memory/reflections.md、memory/entities.md
        content: 要写入的完整 Markdown 内容（覆写整个文件）
    """
    try:
        async with _file_lock:
            lines = write_memory_file(file_path, content)
        log(f"记忆文件更新: {file_path} ({lines} 行)", tag="思维")
        return json.dumps(
            {"ok": True, "path": file_path, "message": f"文件已写入（{lines} 行）"},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    name="delete_memory_file",
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "删除指定的 MD 便签文件。仅限 .md 文件，不允许删除 data/ 目录下的数据库文件。"
        "删除不可撤销，建议先用 read_memory_file 确认内容后再删除。"
    ),
)
async def _tool_delete_memory_file(file_path: str) -> str:
    """删除指定 MD 便签文件。

    Args:
        file_path: 文件路径，如 memory/reflections.md（不可删除 data/ 目录下的文件）
    """
    try:
        async with _file_lock:
            removed = delete_memory_file(file_path)
        if removed:
            log(f"记忆文件已删除: {file_path}", tag="思维")
            return json.dumps({"ok": True, "path": file_path, "message": "文件已删除"}, ensure_ascii=False)
        return json.dumps({"ok": False, "message": f"{file_path} 不存在"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    name="append_memory_file",
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "在 MD 便签文件末尾追加内容，文件不存在时自动创建。"
        "适合新增条目、追加段落，无需读取整个文件，是最安全的写入方式。"
    ),
)
async def _tool_append_memory_file(file_path: str, content: str) -> str:
    """在 MD 便签文件末尾追加内容。

    Args:
        file_path: 文件路径，如 memory/MEMORY.md、memory/reflections.md
        content: 要追加的 Markdown 内容（自动在前一行末尾补换行符）
    """
    try:
        async with _file_lock:
            total_lines = append_to_memory_file(file_path, content)
        log(f"记忆文件追加: {file_path} (共 {total_lines} 行)", tag="思维")
        return json.dumps(
            {"ok": True, "path": file_path, "message": f"内容已追加（当前共 {total_lines} 行）"},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    name="patch_memory_file",
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "在 MD 便签文件中进行精确字符串替换。"
        "old_text 必须来自 read_memory_file 返回的 content 字段（原始内容），"
        "不能使用 view 字段中带行号前缀的文本，否则匹配会失败。"
        "replace_all=true 时替换所有匹配，默认只替换第一处。"
    ),
)
async def _tool_patch_memory_file(
    file_path: str, old_text: str, new_text: str, replace_all: bool = False
) -> str:
    """在 MD 便签文件中进行精确字符串替换。

    Args:
        file_path: 文件路径，如 memory/MEMORY.md
        old_text: 要被替换的原始文本（必须能在文件中精确匹配）
        new_text: 替换后的新文本（可为空字符串，表示删除 old_text）
        replace_all: 是否替换所有匹配项，默认 false（只替换第一处）
    """
    try:
        async with _file_lock:
            result = patch_memory_file_content(file_path, old_text, new_text, replace_all)
        log(f"记忆文件替换: {file_path} 替换 {result['replaced']} 处", tag="思维")
        hint = f"（文件中共有 {result['total_occurrences']} 处匹配）" if result["total_occurrences"] > 1 else ""
        return json.dumps(
            {"ok": True, "path": file_path, "replaced": result["replaced"], "message": f"替换完成{hint}"},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    name="edit_memory_lines",
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "按行号范围替换或插入 MD 便签文件中的内容（1-indexed，闭区间）。"
        "行号来自 read_memory_file/read_section 返回的 view 字段。"
        "new_content 为空字符串时删除该行范围；end_line=-1 表示文件最后一行。"
        "纯插入模式：设 start_line=N+1, end_line=N 可在第 N 行后插入而不替换任何行。"
        "连续多次行编辑时行号会变化，建议每次操作后重新读取。"
    ),
)
async def _tool_edit_memory_lines(
    file_path: str, start_line: int, end_line: int, new_content: str
) -> str:
    """按行号范围替换或插入 MD 便签文件中的内容。

    Args:
        file_path: 文件路径，如 memory/MEMORY.md
        start_line: 起始行号（1-indexed）；纯插入时设为 end_line+1
        end_line: 结束行号（1-indexed，含）；-1 表示最后一行
        new_content: 替换/插入内容（多行用 \\n 分隔；空字符串表示删除该行范围）
    """
    try:
        async with _file_lock:
            result = edit_file_lines(file_path, start_line, end_line, new_content)
        is_insert = end_line >= 0 and start_line > end_line
        if is_insert:
            pos = "文件开头" if end_line == 0 else f"第 {end_line} 行后"
            msg = f"在{pos}插入（当前共 {result['total_lines']} 行）"
        else:
            msg = f"第 {start_line}~{end_line} 行已替换（当前共 {result['total_lines']} 行）"
        log(f"记忆文件行编辑: {file_path} {msg}", tag="思维")
        return json.dumps(
            {"ok": True, "path": file_path, "total_lines": result["total_lines"], "message": msg},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ------------------------------------------------------------------
# 段落级工具（基于 Markdown 标题锚定）
# ------------------------------------------------------------------

@deferred_tool(
    name="view_memory_outline",
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "查看 MD 便签文件的 Markdown 标题大纲（仅标题行和行号，不含正文）。"
        "适合先浏览文件结构再决定读取/编辑哪个段落，大幅节省 token 消耗。"
    ),
)
async def _tool_view_memory_outline(file_path: str) -> str:
    """查看 MD 便签文件的标题大纲。

    Args:
        file_path: 文件路径，如 memory/MEMORY.md
    """
    try:
        result = view_file_outline(file_path)
        return json.dumps({"ok": True, "path": file_path, **result}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    name="read_section",
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "按 Markdown 标题读取 MD 便签文件中的指定段落。"
        "heading 参数需包含 # 号，如 '## 待办事项'、'### 2025-03'。"
        "返回 content（段落 body 原始内容，不含标题行）和 view（含标题行的带行号显示）。"
        "建议先用 view_memory_outline 查看标题列表。"
    ),
)
async def _tool_read_section(file_path: str, heading: str) -> str:
    """按标题读取指定段落。

    Args:
        file_path: 文件路径，如 memory/MEMORY.md
        heading: Markdown 标题全文（含 # 号），如 '## 待办事项'
    """
    try:
        result = read_section_content(file_path, heading)
        return json.dumps({"ok": True, "path": file_path, **result}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    name="write_section",
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "替换或创建 MD 便签文件中指定标题段落的内容。"
        "heading 存在时：替换该段落的 body（标题行自动保留），content 为新的段落内容。"
        "heading 不存在时：创建新段落，after 指定插入位置（在该标题段落之后），"
        "after 为空则追加到文件末尾。"
        "content 为空字符串时，段落变为只有标题行（用于清空段落内容）。"
    ),
)
async def _tool_write_section(
    file_path: str, heading: str, content: str, after: str = ""
) -> str:
    """替换或创建指定标题段落的内容。

    Args:
        file_path: 文件路径，如 memory/MEMORY.md
        heading: Markdown 标题全文（含 # 号），如 '## 待办事项'
        content: 段落的新内容（不含标题行，标题行自动保留/生成）
        after: 新建段落时的插入锚点标题（仅 heading 不存在时生效），为空则追加到文件末尾
    """
    try:
        async with _file_lock:
            result = write_section_content(file_path, heading, content, after)
        action = "替换" if result["action"] == "replaced" else "创建"
        log(f"记忆段落{action}: {file_path} [{heading}] -> 共 {result['total_lines']} 行", tag="思维")
        msg = f"段落 {heading} 已{action}（当前共 {result['total_lines']} 行）"
        return json.dumps(
            {"ok": True, "path": file_path, **result, "message": msg},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(
    name="delete_section",
    group="notes", tags=["core", "reflect"], source="mind.notes",
    description=(
        "删除 MD 便签文件中指定标题段落（含标题行和全部内容，包括子标题）。"
        "删除不可撤销。heading 参数需包含 # 号，如 '## 待办事项'。"
    ),
)
async def _tool_delete_section(file_path: str, heading: str) -> str:
    """删除指定标题段落。

    Args:
        file_path: 文件路径，如 memory/MEMORY.md
        heading: 要删除的 Markdown 标题全文（含 # 号），如 '## 待办事项'
    """
    try:
        async with _file_lock:
            result = delete_section_content(file_path, heading)
        log(f"记忆段落删除: {file_path} [{heading}] 删除 {result['deleted_lines']} 行", tag="思维")
        msg = f"段落 {heading} 已删除（{result['deleted_lines']} 行，当前共 {result['total_lines']} 行）"
        return json.dumps(
            {"ok": True, "path": file_path, **result, "message": msg},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
