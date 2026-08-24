"""ASR 语音识别协议适配器：收口不同供应商的音频转写 API 差异。

OpenAI 系走 multipart `/audio/transcriptions`；DashScope（含 token-plan 网关）
走多模态生成端点，音频以 data:base64 URI 内联进 messages，响应无 choices 结构
（文本在 sentence/text 字段，extract_text 容错解析各变体）。

扩展新供应商：实现 AsrAdapter 并调用 register_asr_adapter() 注册；
供应商配置可通过 media_protocol 显式指定适配器，未指定时按 host 规则自动匹配。

Model Experience：① 不注入任何 prompt 层内容，转写文本仅作为工具结果返回；
② token 影响为纯增量（转写文本），经 result_budget 截断；③ 不触碰缓存前缀层。
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple

from agent.llm.adapter_base import AdapterRegistry, AdapterRequest, host_root


class AsrAdapter(ABC):
    """ASR 协议适配器基类。"""

    name: str = ""

    @abstractmethod
    def build_transcribe_request(
        self,
        base_url: str,
        *,
        model: str,
        audio_data: bytes,
        file_name: str,
        mime_type: str,
    ) -> AdapterRequest:
        """构建音频转写请求。"""

    @abstractmethod
    def extract_text(self, result: Dict[str, Any]) -> str:
        """从响应 JSON 提取转写文本。"""


def _file_ext(file_name: str) -> str:
    """取音频文件扩展名（小写，无点），缺省 mp3。"""
    return file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "mp3"


class OpenAIAsrAdapter(AsrAdapter):
    """OpenAI 风格：POST {base_url}/audio/transcriptions（multipart 表单）。"""

    name = "openai"
    _PATH = "/audio/transcriptions"

    def build_transcribe_request(
        self,
        base_url: str,
        *,
        model: str,
        audio_data: bytes,
        file_name: str,
        mime_type: str,
    ) -> AdapterRequest:
        return AdapterRequest(
            url=f"{base_url}{self._PATH}",
            # 部分服务对空 model 字段报错，空时不传
            payload={"model": model} if model else {},
            files={"file": (file_name, audio_data, mime_type)},
        )

    def extract_text(self, result: Dict[str, Any]) -> str:
        return result.get("text", "")


class DashScopeAsrAdapter(AsrAdapter):
    """DashScope 原生同步 ASR（短音频 ≤5 分钟）。

    接口挂在网关机根路径，与 base_url 中的聊天协议路径无关；音频内联为
    data:base64 URI（≤10MB），响应无 choices 字段，文本在 sentence/text 各层。
    """

    name = "dashscope"
    _PATH = "/api/v1/services/aigc/multimodal-generation/generation"

    def build_transcribe_request(
        self,
        base_url: str,
        *,
        model: str,
        audio_data: bytes,
        file_name: str,
        mime_type: str,
    ) -> AdapterRequest:
        data_uri = f"data:{mime_type};base64,{base64.b64encode(audio_data).decode()}"
        payload: Dict[str, Any] = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {"data": data_uri}},
            ]}]},
            "parameters": {"format": _file_ext(file_name)},
        }
        return AdapterRequest(url=f"{host_root(base_url)}{self._PATH}", payload=payload)

    def extract_text(self, result: Dict[str, Any]) -> str:
        output = result.get("output")
        if isinstance(output, dict):
            choices = output.get("choices")
            if choices:
                content = (choices[0].get("message") or {}).get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "".join(
                        item.get("text", "") for item in content if isinstance(item, dict)
                    )
            for container in (output, result):
                sentence = container.get("sentence")
                if isinstance(sentence, dict) and sentence.get("text"):
                    return sentence["text"]
                if container.get("text"):
                    return container["text"]
        sentence = result.get("sentence")
        if isinstance(sentence, dict) and sentence.get("text"):
            return sentence["text"]
        return result.get("text", "")


_REGISTRY: AdapterRegistry[AsrAdapter] = AdapterRegistry("ASR")


def register_asr_adapter(
    adapter: AsrAdapter,
    *,
    host_keywords: Tuple[str, ...] = (),
    default: bool = False,
) -> None:
    """注册 ASR 协议适配器（语义见 AdapterRegistry.register）。"""
    _REGISTRY.register(adapter, host_keywords=host_keywords, default=default)


def resolve_asr_adapter(base_url: str, protocol: str = "") -> AsrAdapter:
    """解析 ASR 协议适配器（语义见 AdapterRegistry.resolve）。"""
    return _REGISTRY.resolve(base_url, protocol)


register_asr_adapter(OpenAIAsrAdapter(), default=True)
register_asr_adapter(DashScopeAsrAdapter(), host_keywords=("aliyuncs.com",))
