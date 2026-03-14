from __future__ import annotations

from typing import Optional

from agent.core.respond.input_senses.sense_unit_interface import SenseUnit
from core.tags import media_file_tag


class SensePath(SenseUnit):
    """
    内置文件感知单元。\n
    目前先保留旧实现的"占位"行为；后续可扩展为：读取文件、摘要、MCP file tools 等。
    """

    def __init__(self) -> None:
        self.sense_tag = media_file_tag

    @classmethod
    async def do_processing(cls, process_content: str) -> Optional[str]:
        # TODO: 后续扩展为真实的文件路径/内容处理
        return "路径"
