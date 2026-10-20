"""表情包与图片感知实体 — 收藏/检索/发送表情包，全量图片语义索引（文搜图/图搜图）。"""

from core.entity import EntityRegistry

EntityRegistry.register_group_order("sticker", 36)


def register_lifecycle() -> None:
    """托管图片库连接，由宿主在索引与向量回填停止后关闭。"""
    from core.lifecycle import Lifecycle

    from .store import get_sticker_store

    store = get_sticker_store()
    Lifecycle.register("sticker_store", store, cleanup=store.close)
