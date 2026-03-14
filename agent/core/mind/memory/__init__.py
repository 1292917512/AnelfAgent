"""memory：语义记忆系统（FTS5 + Embedding 混合检索 + 内部工具 + 便签记忆）。"""

from .memory_types import MemoryEntry, MemorySearchResult, MemoryType
from .memory_store import MemoryStore
from .memory_retriever import MemoryRetriever
from .memory_utils import cosine_similarity, hash_text, list_workspace_md_files, pack_embedding, unpack_embedding
from .embedder import Embedder
from .tools import register_memory_tools
from .notes import register_notes_tools

__all__ = [
    "MemoryEntry",
    "MemorySearchResult",
    "MemoryType",
    "MemoryStore",
    "MemoryRetriever",
    "Embedder",
    "register_memory_tools",
    "register_notes_tools",
    "cosine_similarity",
    "hash_text",
    "list_workspace_md_files",
    "pack_embedding",
    "unpack_embedding",
]
