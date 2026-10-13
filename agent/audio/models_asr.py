"""内部模型 ASR 提供者 — 桥接模型配置（llm_clients.json）中 asr 类型的模型链。

让"内部模型利用"与第三方组件（FunASR 等）在同一条 ASR 优先级链上竞争：
本地组件默认优先（priority 小），云端模型链作为内置兜底（默认 priority 50，
经 audio_models_asr_priority 调整）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from agent.audio.providers import KIND_ASR
from core.config import get_config_int


class ModelsAsrProvider:
    """模型配置 ASR 提供者：按 llm_clients.json 的 asr 类型优先级逐模型回退。"""

    name = "models"
    kind = KIND_ASR

    @property
    def priority(self) -> int:
        return get_config_int("audio_models_asr_priority", 50)

    async def check_available(self) -> bool:
        try:
            from agent.llm.model_api import get_model_api
            return bool(get_model_api().list_models("asr"))
        except Exception:
            return False

    async def transcribe(
        self, audio_path: str, source_time: str = "",
    ) -> List[Dict[str, Any]]:
        from agent.llm.model_api import get_model_api
        with open(audio_path, "rb") as f:
            audio_data = f.read()
        file_name = os.path.basename(audio_path)
        result = await get_model_api().transcribe(audio_data, file_name=file_name)
        return [{"start_ms": 0, "end_ms": 0, "text": result.value, "vector": None,
                 "abs_start_ms": None, "abs_end_ms": None}]


def register_models_asr() -> None:
    """注册内部模型 ASR 提供者（bootstrap 调用一次，同名覆盖幂等）。"""
    from agent.audio.providers import get_audio_registry
    get_audio_registry().register(ModelsAsrProvider())
