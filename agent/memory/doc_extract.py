"""文档文本提取：PDF / Word / 纯文本 → 纯文本，供记忆索引切块使用。"""

from __future__ import annotations

from pathlib import Path

# 可索引的文档扩展名（.md 同时属于便签体系）
SUPPORTED_DOC_EXTS = frozenset({".pdf", ".docx", ".txt", ".md"})


def extract_document_text(path: Path) -> str:
    """提取文档纯文本内容。不支持的类型或无可用文本时抛 ValueError。"""
    ext = path.suffix.lower()
    if ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext == ".docx":
        return _extract_docx(path)
    raise ValueError(f"不支持的文档类型: {ext or path.name}")


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n\n".join(p for p in pages if p)
    if not text:
        raise ValueError(f"PDF 无可用文本: {path.name}")
    return text


def _extract_docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs)
    if not text:
        raise ValueError(f"DOCX 无可用文本: {path.name}")
    return text
