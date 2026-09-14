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
from core.log import log

_LOG_TAG = "音频"


class ModelsAsrProvider:
    """模型配置 ASR 提供者：按 llm_clients.json 的 asr 类型优先级逐模型回退。"""

    name = "models"
    kind = KIND_ASR

    @property
    def priority(self) -> int:
        return get_config_int("audio_models_asr_priority", 50)

    async def check_available(self) -> bool:
        try:
            from agent.llm import get_llm_manager
            return bool(get_llm_manager().iter_media_for_type("asr"))
        except Exception:
            return False

    async def transcribe(
        self, audio_path: str, source_time: str = "",
    ) -> List[Dict[str, Any]]:
        from agent.llm import get_llm_manager
        pairs = get_llm_manager().iter_media_for_type("asr")
        if not pairs:
            raise RuntimeError("未配置 asr 类型模型")
        with open(audio_path, "rb") as f:
            audio_data = f.read()
        file_name = os.path.basename(audio_path)
        last_err = ""
        for model_name, client in pairs:
            try:
                text = await client.transcribe(audio_data, model=model_name, file_name=file_name)
                return [{"start_ms": 0, "end_ms": 0, "text": text, "vector": None,
                         "abs_start_ms": None, "abs_end_ms": None}]
            except Exception as exc:
                last_err = str(exc).strip() or type(exc).__name__
                log(f"ASR 模型 {model_name} 转写失败，尝试下一个: {last_err}",
                    "WARNING", tag=_LOG_TAG)
                continue
        raise RuntimeError(f"所有 asr 模型均调用失败: {last_err}")


def register_models_asr() -> None:
    """注册内部模型 ASR 提供者（bootstrap 调用一次，同名覆盖幂等）。"""
    from agent.audio.providers import get_audio_registry
    get_audio_registry().register(ModelsAsrProvider())
