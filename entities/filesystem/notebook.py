"""Jupyter Notebook 单元格编辑（对齐 Claude Code NotebookEdit）。

edit_file 对 .ipynb 拒绝并引导到本工具（JSON 结构直接做字符串替换易损坏）；
read_file 对 .ipynb 返回 cell 列表摘要而非二进制元数据。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from entities._sdk import tool


def _load_notebook(fp: str) -> Dict[str, Any]:
    with open(fp, "r", encoding="utf-8") as f:
        nb = json.load(f)
    if not isinstance(nb.get("cells"), list):
        raise ValueError("不是有效的 notebook 格式（缺少 cells）")
    return nb


def _save_notebook(fp: str, nb: Dict[str, Any]) -> None:
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)
        f.write("\n")


def summarize_notebook(fp: str) -> str:
    """cell 列表摘要（read_file 的 .ipynb 分发）。"""
    nb = _load_notebook(fp)
    cells = nb["cells"]
    lines: List[str] = [f"notebook 共 {len(cells)} 个 cell:"]
    for i, cell in enumerate(cells[:50]):
        source = "".join(cell.get("source", []))
        preview = source.split("\n")[:3]
        preview_text = "\n".join(f"    {p}" for p in preview)
        if len(source.split("\n")) > 3:
            preview_text += "\n    ..."
        lines.append(f"cell[{i}] ({cell.get('cell_type', '?')}):\n{preview_text or '    (空)'}")
    if len(cells) > 50:
        lines.append(f"... 其余 {len(cells) - 50} 个 cell 省略，用 notebook_edit 按索引操作")
    lines.append("提示：用 notebook_edit 按 cell_index 编辑单元格。")
    return "\n".join(lines)


@tool(name="notebook_edit", group="os")
def notebook_edit(path: str, cell_index: int, new_source: str = "",
                  cell_type: str = "", edit_mode: str = "replace") -> str:
    """编辑 Jupyter notebook（.ipynb）的单元格。

    Args:
        path: notebook 文件路径（相对于 workspace）
        cell_index: 目标单元格索引（从 0 开始）
        new_source: 新的单元格内容（replace/insert 时使用）
        cell_type: 单元格类型 code/markdown（replace/insert 时可选，默认保持原类型或 code）
        edit_mode: 操作模式：replace（替换，默认）、insert（在该索引处插入）、delete（删除）
    """
    try:
        from entities.filesystem.tools import _safe_path
        fp = _safe_path(path)
        if not fp.lower().endswith(".ipynb"):
            return json.dumps({"error": "notebook_edit 仅支持 .ipynb 文件"}, ensure_ascii=False)
        if not os.path.isfile(fp):
            return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)

        nb = _load_notebook(fp)
        cells: List[Dict[str, Any]] = nb["cells"]

        if edit_mode == "delete":
            if not (0 <= cell_index < len(cells)):
                return json.dumps({"error": f"cell_index 越界: {cell_index}（共 {len(cells)} 个 cell）"},
                                  ensure_ascii=False)
            removed = cells.pop(cell_index)
            _save_notebook(fp, nb)
            return json.dumps({"ok": True, "message": f"已删除 cell[{cell_index}]"
                               f"（{removed.get('cell_type', '?')}），剩余 {len(cells)} 个 cell"},
                              ensure_ascii=False)

        if cell_type not in ("", "code", "markdown"):
            return json.dumps({"error": "cell_type 只能是 code 或 markdown"}, ensure_ascii=False)

        new_cell = {
            "cell_type": cell_type or "code",
            "metadata": {},
            "source": new_source.splitlines(keepends=True),
            **({"outputs": [], "execution_count": None} if (cell_type or "code") == "code" else {}),
        }

        if edit_mode == "insert":
            cell_index = max(0, min(cell_index, len(cells)))
            cells.insert(cell_index, new_cell)
            message = f"已在索引 {cell_index} 处插入 {new_cell['cell_type']} cell"
        elif edit_mode == "replace":
            if not (0 <= cell_index < len(cells)):
                return json.dumps({"error": f"cell_index 越界: {cell_index}（共 {len(cells)} 个 cell）"},
                                  ensure_ascii=False)
            old = cells[cell_index]
            if not cell_type:
                new_cell["cell_type"] = old.get("cell_type", "code")
                if new_cell["cell_type"] == "code":
                    new_cell.setdefault("outputs", [])
                    new_cell.setdefault("execution_count", None)
                else:
                    new_cell.pop("outputs", None)
                    new_cell.pop("execution_count", None)
            new_cell["metadata"] = old.get("metadata", {})
            cells[cell_index] = new_cell
            message = f"已替换 cell[{cell_index}]（{new_cell['cell_type']}）"
        else:
            return json.dumps({"error": f"未知的 edit_mode: {edit_mode}（支持 replace/insert/delete）"},
                              ensure_ascii=False)

        _save_notebook(fp, nb)
        # 刷新读取状态（若该文件被读过，避免后续操作误判过期）
        try:
            from entities.filesystem import file_state
            if file_state.get_cache().get(fp) is not None:
                with open(fp, "r", encoding="utf-8") as f:
                    file_state.record_write(fp, f.read(), os.path.getmtime(fp))
        except Exception:
            pass
        return json.dumps({"ok": True, "path": fp, "message": message}, ensure_ascii=False)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"notebook JSON 解析失败: {e}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
