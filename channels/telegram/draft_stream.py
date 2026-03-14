"""流式草稿预览 -- 参照 openclaw draft-stream.ts。

通过不断编辑同一条 Telegram 消息实现流式输出效果。
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional, Union

from core.log import log

from . import send as tg_send
from .format import markdown_to_telegram_html
from .types import ThreadSpec

MIN_INITIAL_CHARS = 30
MAX_MESSAGE_LEN = 4096
EDIT_DEBOUNCE_MS = 800


class TelegramDraftStream:
    """通过不断编辑同一条消息实现流式输出效果。

    行为：
    1. 累积文本直到达到 min_initial_chars，然后创建首条消息
    2. 后续通过 editMessageText 更新内容
    3. 文本超过 MAX_MESSAGE_LEN 时归档当前消息并创建新消息
    4. finalize() 完成输出，用格式化版本替换最终内容
    """

    def __init__(
        self,
        bot: Any,
        chat_id: Union[str, int],
        *,
        thread: Optional[ThreadSpec] = None,
        reply_to_message_id: Optional[int] = None,
        min_initial_chars: int = MIN_INITIAL_CHARS,
        max_chars: int = MAX_MESSAGE_LEN,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread = thread
        self._reply_to = reply_to_message_id
        self._min_initial = min_initial_chars
        self._max_chars = max_chars

        self._buffer: str = ""
        self._current_message_id: Optional[int] = None
        self._archived_ids: List[int] = []
        self._last_edit_text: str = ""
        self._edit_lock = asyncio.Lock()
        self._finalized = False

    @property
    def has_message(self) -> bool:
        return self._current_message_id is not None

    @property
    def all_message_ids(self) -> List[int]:
        ids = list(self._archived_ids)
        if self._current_message_id:
            ids.append(self._current_message_id)
        return ids

    async def push(self, text: str) -> None:
        """追加文本。达到阈值时自动创建/更新消息。"""
        if self._finalized:
            return
        self._buffer += text

        if not self._current_message_id:
            if len(self._buffer) >= self._min_initial:
                await self._create_message(self._buffer)
            return

        if len(self._buffer) > self._max_chars:
            await self._archive_and_create_new()
            return

        await self._edit_current(self._buffer)

    async def finalize(self, final_text: str) -> List[int]:
        """完成流式输出。用格式化版本替换最终内容，返回所有 message_id。"""
        if self._finalized:
            return self.all_message_ids
        self._finalized = True

        if not self._current_message_id:
            html = markdown_to_telegram_html(final_text)
            msg_id = await tg_send.send_text(
                self._bot, self._chat_id, html,
                parse_mode="HTML",
                reply_to_message_id=self._reply_to,
                thread=self._thread,
            )
            return self._archived_ids + [msg_id]

        html = markdown_to_telegram_html(final_text)
        if len(html) <= self._max_chars:
            await tg_send.edit_message_text(
                self._bot, self._chat_id, self._current_message_id,
                html, parse_mode="HTML",
            )
        else:
            await tg_send.edit_message_text(
                self._bot, self._chat_id, self._current_message_id,
                markdown_to_telegram_html(self._buffer),
                parse_mode="HTML",
            )

        return self.all_message_ids

    async def cancel(self) -> None:
        """取消并尝试删除所有草稿消息。"""
        self._finalized = True
        for mid in self.all_message_ids:
            await tg_send.delete_message(self._bot, self._chat_id, mid)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _create_message(self, text: str) -> None:
        display = text + " ▍"
        msg_id = await tg_send.send_text(
            self._bot, self._chat_id, display,
            parse_mode=None,
            reply_to_message_id=self._reply_to,
            thread=self._thread,
        )
        self._current_message_id = msg_id
        self._last_edit_text = display
        self._reply_to = None

    async def _edit_current(self, text: str) -> None:
        if not self._current_message_id:
            return
        display = text + " ▍"
        if display == self._last_edit_text:
            return
        async with self._edit_lock:
            ok = await tg_send.edit_message_text(
                self._bot, self._chat_id, self._current_message_id,
                display, parse_mode=None,
            )
            if ok:
                self._last_edit_text = display

    async def _archive_and_create_new(self) -> None:
        if self._current_message_id:
            await tg_send.edit_message_text(
                self._bot, self._chat_id, self._current_message_id,
                self._buffer[:self._max_chars],
                parse_mode=None,
            )
            self._archived_ids.append(self._current_message_id)
            self._current_message_id = None

        overflow = self._buffer[self._max_chars:]
        self._buffer = overflow
        if overflow:
            await self._create_message(overflow)
