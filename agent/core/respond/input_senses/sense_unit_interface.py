from __future__ import annotations

from typing import Optional

from core.tags import Tag


class SenseUnit:
    """
    感知单元：当输入文本中出现某类 tag 时，对其进行“感知替换”。\n
    例如 `[file:xxx]` -> `[file:xxx的摘要/路径/内容]`。
    """

    sense_tag: Optional[Tag] = None

    async def processing_sensory(self, tag: tuple[str, str]) -> Optional[str]:
        if not self.sense_tag:
            return None
        if process_content := self.sense_tag.match_label(tag):
            processed = await self.do_processing(process_content)
            if processed is None:
                return None
            return self.sense_tag.generate_label(processed)
        return None

    @classmethod
    async def do_processing(cls, process_content: str) -> Optional[str]:
        raise NotImplementedError

