"""表情包服务 -- 表情包与图片索引管理（封装 entities.sticker 与向量回填）。"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from core.log import log


class StickerServiceError(Exception):
    """表情包服务错误（status_code 供路由层映射 HTTP 状态码）。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class StickerService:
    """表情包与图片索引管理服务（Web 侧入口）。"""

    @staticmethod
    def _store() -> Any:
        from entities.sticker.store import get_sticker_store
        return get_sticker_store()

    # ------------------------------------------------------------------
    # 全量图片索引
    # ------------------------------------------------------------------

    async def list_images(self, page: int, page_size: int) -> Dict[str, Any]:
        """分页浏览全量图片感知索引。"""
        return await self._store().list_images(page=page, page_size=page_size)

    async def get_image(self, path: str) -> Optional[Dict[str, Any]]:
        """查询单张索引图片记录（不存在返回 None）。"""
        return await self._store().get_image(path)

    async def delete_image(self, path: str) -> bool:
        """从索引中移除一张图片（不删除原文件），返回是否移除成功。"""
        return await self._store().delete_image(path)

    # ------------------------------------------------------------------
    # 向量维度健康与重建
    # ------------------------------------------------------------------

    @staticmethod
    def _mismatched_count(embedding: Dict[str, Any], model_dims: Optional[int]) -> int:
        """统计与参考维度不一致的向量条数（参考维度优先取当前模型，其次 vec 索引维度）。"""
        total = 0
        for kind, dims_map in embedding["stored_dims"].items():
            ref = model_dims or embedding["vec_dims"].get(kind)
            if not ref:
                continue
            total += sum(c for d, c in dims_map.items() if int(d) != ref)
        return total

    async def rebuild_embeddings(self, mode: str) -> Dict[str, Any]:
        """切换向量模型后重建贴纸/图片向量：清理后由后台 EmbeddingWorker 按当前模型回填。

        Args:
            mode: "mismatched" 仅清维度不一致的向量；"all" 全量清空。

        Returns:
            {"ok", "dims", "cleared"} 结果字典。

        Raises:
            StickerServiceError: 模式非法（400）或无可用向量模型（503）。
        """
        from entities.sticker.tools import get_embedder

        if mode not in ("mismatched", "all"):
            raise StickerServiceError(f"不支持的重建模式: {mode}", status_code=400)

        embedder = get_embedder()
        dims = embedder.dimensions
        if dims is None:
            probe = await embedder.embed_query("dimension probe")
            if probe:
                dims = len(probe)
        if not dims:
            raise StickerServiceError("无可用向量模型，无法重建", status_code=503)

        store = self._store()
        cleared: Dict[str, Any]
        if mode == "all":
            cleared = {"total": await store.clear_embeddings()}
        else:
            cleared = await store.clear_mismatched_embeddings(dims)

        from agent.memory.embedding import wake_embedding_worker
        wake_embedding_worker()
        log(f"贴纸向量重建（{mode}）: 目标维度 {dims}，已清空 {cleared}", tag="贴纸")
        return {"ok": True, "dims": dims, "cleared": cleared}

    # ------------------------------------------------------------------
    # 表情包
    # ------------------------------------------------------------------

    async def list_stickers(self, query: str, page: int, page_size: int) -> Dict[str, Any]:
        """分页列出表情包（query 非空时在当前页内做模糊过滤）。"""
        return await self._store().list_stickers(page=page, page_size=page_size, query=query)

    async def stats(self) -> Dict[str, Any]:
        """表情包与图片索引统计（含向量维度健康）。"""
        from entities.sticker.tools import get_embedder

        stats = await self._store().stats()
        model = ""
        model_dims: Optional[int] = None
        try:
            embedder = get_embedder()
            model = embedder.client_name
            model_dims = embedder.dimensions
        except Exception as exc:
            log(f"获取向量模型信息失败: {exc}", "DEBUG", tag="贴纸")
        embedding = stats["embedding"]
        embedding["model"] = model
        embedding["model_dims"] = model_dims
        embedding["mismatched"] = self._mismatched_count(embedding, model_dims)
        return stats

    async def get_sticker(self, sticker_id: str) -> Optional[Dict[str, Any]]:
        """查询单个表情包（不存在返回 None）。"""
        return await self._store().get_sticker(sticker_id)

    async def import_sticker(
        self,
        tmp_path: str,
        description: str,
        tags: str,
        emotion: str,
    ) -> Dict[str, Any]:
        """上传导入全流程：哈希/感知哈希/描述生成/归档/向量/入库。

        Args:
            tmp_path: 已落盘的临时文件路径（调用方负责清理）。
            description: 描述文本，留空时自动调用视觉模型生成。
            tags: 逗号/空格分隔的标签字符串。
            emotion: 情绪标注。

        Returns:
            新建的表情包记录。
        """
        from entities.sticker.phash import compute_phash
        from entities.sticker.tools import (
            describe_sticker,
            embed_for_index,
            import_to_stickers_dir,
            md5_file,
            parse_tags,
        )

        content_hash = md5_file(tmp_path)
        phash = compute_phash(tmp_path)
        tag_list = parse_tags(tags)
        if not description.strip():
            description = await describe_sticker(tmp_path)
        dest = import_to_stickers_dir(tmp_path, content_hash)
        embedding = await embed_for_index(description, tag_list, dest)

        sticker = await self._store().add_sticker(
            file_path=dest,
            description=description,
            tags=tag_list,
            emotion=emotion.strip(),
            content_hash=content_hash,
            phash=phash,
            source="webui",
            embedding=embedding,
        )
        log(f"WebUI 上传表情包: {sticker['id']}", tag="贴纸")
        return sticker

    async def update_sticker(
        self,
        sticker_id: str,
        description: Optional[str],
        tags: Optional[List[str]],
        emotion: Optional[str],
    ) -> Dict[str, Any]:
        """更新描述/标签/情绪（自动重新生成检索向量）。

        Raises:
            StickerServiceError: 表情包不存在（404）。
        """
        from entities.sticker.tools import embed_for_index

        store = self._store()
        current = await store.get_sticker(sticker_id)
        if not current:
            raise StickerServiceError("表情包不存在", status_code=404)

        new_desc = description if description is not None else current["description"]
        new_tags = tags if tags is not None else current["tags"]
        embedding = await embed_for_index(new_desc, new_tags, current["file_path"])

        return await store.update_sticker(
            sticker_id,
            description=description,
            tags=tags,
            emotion=emotion,
            embedding=embedding,
        )

    async def reindex_sticker(self, sticker_id: str) -> Dict[str, Any]:
        """重新生成描述与检索向量（视觉模型重描述）。

        Raises:
            StickerServiceError: 表情包不存在/文件丢失（404）或无可用视觉模型（503）。
        """
        from entities.sticker.tools import describe_sticker, embed_for_index

        store = self._store()
        current = await store.get_sticker(sticker_id)
        if not current:
            raise StickerServiceError("表情包不存在", status_code=404)
        if not os.path.isfile(current["file_path"]):
            raise StickerServiceError("表情包文件已丢失", status_code=404)

        description = await describe_sticker(current["file_path"])
        if not description:
            raise StickerServiceError("无可用视觉模型，无法重新生成描述", status_code=503)
        embedding = await embed_for_index(description, current["tags"], current["file_path"])
        return await store.update_sticker(
            sticker_id, description=description, embedding=embedding)

    async def delete_sticker(self, sticker_id: str) -> Optional[Dict[str, Any]]:
        """删除表情包（连同文件与索引），不存在返回 None。"""
        store = self._store()
        removed = await store.delete_sticker(sticker_id)
        if not removed:
            return None
        try:
            if os.path.exists(removed["file_path"]):
                os.remove(removed["file_path"])
        except OSError as exc:
            log(f"表情包文件删除失败: {exc}", "DEBUG", tag="贴纸")
        return removed

    @staticmethod
    def stickers_dir() -> str:
        """表情包存储目录（不存在时创建）。"""
        from entities.sticker.tools import stickers_dir
        return stickers_dir()
