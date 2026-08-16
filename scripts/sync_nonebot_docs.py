#!/usr/bin/env python3
"""NoneBot 官方文档 vendor 同步脚本。

按 ``docs/README.md`` 索引（或文件内 ``<!-- source: ... -->`` 标注）把
nonebot.dev 官方文档的最新原文拉取到 ``channels/nonebot_bridge/docs/``，
保持本地参考文档与上游版本一致。

源映射：官方链接 ``https://nonebot.dev/docs/<path>`` 对应 GitHub 仓库
nonebot/nonebot2（master 分支）``website/docs/<path>.mdx|.md``，
同步时剥离 Docusaurus frontmatter 并回写 source 标注。

``store/`` 目录为本地撰写的商店说明（无官方 md 对应），跳过。

用法：
    python scripts/sync_nonebot_docs.py           # 同步全部
    python scripts/sync_nonebot_docs.py -v        # 显示每文件结果
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "channels" / "nonebot_bridge" / "docs"
RAW_BASE = "https://raw.githubusercontent.com/nonebot/nonebot2/master/website/docs"
DOCS_BASE = "https://nonebot.dev/docs"

_SOURCE_RE = re.compile(r"^<!--\s*source:\s*(\S+)\s*-->", re.MULTILINE)
_INDEX_ROW_RE = re.compile(r"\|\s*\[([^\]]+)\]\(([^)]+)\)[^|]*\|[^|]*\|\s*\[[^\]]*\]\(([^)]+)\)")
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

_FETCH_INTERVAL = 0.2
_TIMEOUT = 30


def build_index_mapping() -> Dict[str, str]:
    """解析 docs/README.md 索引表：文件相对路径 → 官方链接。"""
    mapping: Dict[str, str] = {}
    readme = DOCS_DIR / "README.md"
    if not readme.exists():
        return mapping
    for match in _INDEX_ROW_RE.finditer(readme.read_text("utf-8")):
        file_path, link = match.group(1), match.group(3)
        if link.startswith(DOCS_BASE):
            mapping[file_path] = link
    return mapping


def fetch_url(url: str) -> Optional[str]:
    """拉取 URL 文本，失败返回 None。"""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "anelfagent-docs-sync"})
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"  拉取失败 {url}: {exc}", file=sys.stderr)
        return None


def strip_frontmatter(content: str) -> str:
    """剥离 Docusaurus frontmatter（--- ... --- 块）。"""
    return _FRONTMATTER_RE.sub("", content, count=1).lstrip("\n")


def resolve_raw_content(official_link: str) -> Optional[tuple[str, str]]:
    """官方链接 → (raw 地址, 原文内容)，依次尝试 .mdx / .md / 目录 README。"""
    path = official_link[len(DOCS_BASE):].strip("/")
    for candidate in (
        f"{path}.mdx",
        f"{path}.md",
        f"{path}/index.mdx",
        f"{path}/index.md",
        f"{path}/README.mdx",
        f"{path}/README.md",
    ):
        url = f"{RAW_BASE}/{candidate}"
        content = fetch_url(url)
        if content is not None and not content.startswith("404:"):
            return url, content
    return None


def sync_file(md_path: Path, official_link: str, verbose: bool) -> bool:
    """同步单个文档文件，返回是否成功更新。"""
    resolved = resolve_raw_content(official_link)
    if resolved is None:
        print(f"  跳过（上游无对应原文）: {md_path.relative_to(DOCS_DIR)}")
        return False
    raw_url, content = resolved

    body = strip_frontmatter(content)
    rendered = f"<!-- source: {official_link} -->\n\n{body}"
    md_path.write_text(rendered, "utf-8")
    if verbose:
        print(f"  已同步: {md_path.relative_to(DOCS_DIR)} <- {raw_url}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 NoneBot 官方文档 vendor 副本")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示每文件结果")
    args = parser.parse_args()

    if not DOCS_DIR.exists():
        print(f"文档目录不存在: {DOCS_DIR}", file=sys.stderr)
        return 1

    index_map = build_index_mapping()
    updated, skipped, failed = 0, 0, 0

    for md_path in sorted(DOCS_DIR.rglob("*.md")):
        rel = md_path.relative_to(DOCS_DIR).as_posix()
        if rel == "README.md" or rel.startswith("store/"):
            # 索引本身与本地撰写的商店说明不在官方 md 源内
            skipped += 1
            continue

        existing = _SOURCE_RE.search(md_path.read_text("utf-8"))
        official_link = existing.group(1) if existing else index_map.get(rel)
        if not official_link:
            print(f"  跳过（无 source 标注且索引未收录）: {rel}", file=sys.stderr)
            skipped += 1
            continue

        if sync_file(md_path, official_link, args.verbose):
            updated += 1
        else:
            failed += 1
        time.sleep(_FETCH_INTERVAL)

    print(f"文档同步完成: 更新 {updated}，跳过 {skipped}，失败 {failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
