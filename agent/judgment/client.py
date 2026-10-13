"""System One HTTP 客户端，供云端与自部署判断服务共用。

契约（docs.typesafe.ai/api）：POST {base_url}/v1/systemone，可选 Bearer 认证；
请求体 state + model + questions（id → 问题），响应 answers 按同键返回。
所有失败统一包装为 JudgmentError，由引擎按归因决策是否降级回退通道。
"""
from __future__ import annotations

from typing import Any, Dict

import httpx
from pydantic import ValidationError

from agent.judgment.types import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    JsonValue,
    JudgmentError,
    JudgmentUsage,
    NoulAnswer,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
    question_payload,
)
from core.config import get_config_float
from core.network import async_network_client
from core.tool_errors import ErrorCause

_ENDPOINT = "/v1/systemone"


class SystemOneClient:
    """System One 协议客户端，统一地址解析、认证和答案校验。"""

    def __init__(self, *, base_url: str, api_key: str, timeout: float) -> None:
        self._endpoint = self.resolve_endpoint(base_url)
        self._api_key = api_key.strip()
        self._timeout = timeout

    @staticmethod
    def resolve_endpoint(base_url: str) -> str:
        """接受服务根地址、版本前缀或完整端点，保留反向代理路径。"""
        try:
            url = httpx.URL(base_url.strip())
        except httpx.InvalidURL as exc:
            raise JudgmentError("判断服务地址无效", cause=ErrorCause.CONFIG, retryable=False) from exc
        if (url.scheme not in {"http", "https"} or not url.host
                or url.userinfo or url.query or url.fragment):
            raise JudgmentError(
                "判断服务地址须为 HTTP(S) 地址，不含用户名、密码、查询参数或片段",
                cause=ErrorCause.CONFIG, retryable=False,
            )
        path = url.path.rstrip("/")
        if path.endswith(_ENDPOINT):
            return str(url.copy_with(path=path))
        suffix = "/systemone" if path.endswith("/v1") else _ENDPOINT
        return str(url.copy_with(path=path + suffix))

    async def evaluate(
        self,
        *,
        state: JsonValue,
        model: str,
        questions: Dict[str, Question],
    ) -> tuple[Dict[str, Answer], str, JudgmentUsage]:
        """批量评判问题，返回（答案字典, 实际模型版本, 用量）。"""
        payload = {
            "state": state,
            "model": model,
            "questions": {qid: question_payload(q) for qid, q in questions.items()},
        }
        data = await self._post(payload)
        return self._parse_response(data, questions)

    async def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with async_network_client(
                timeout=httpx.Timeout(self._timeout, connect=min(self._timeout, get_config_float("judgment_connect_timeout", 2.0))),
                headers={"Authorization": f"Bearer {self._api_key}"} if self._api_key else {},
            ) as client:
                resp = await client.post(self._endpoint, json=payload)
        except httpx.TimeoutException as exc:
            raise JudgmentError(
                "System One 请求超时", cause=ErrorCause.TIMEOUT, retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise JudgmentError(
                f"System One 网络错误: {exc}", cause=ErrorCause.NETWORK, retryable=True
            ) from exc
        except ValueError as exc:
            raise JudgmentError(f"判断服务网络配置无效: {exc}", cause=ErrorCause.CONFIG, retryable=False) from exc

        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError as exc:
                raise JudgmentError(
                    "System One 响应不是合法 JSON", cause=ErrorCause.INTERNAL, retryable=True
                ) from exc
            if not isinstance(data, dict):
                raise JudgmentError(
                    "System One 响应结构无效", cause=ErrorCause.INTERNAL, retryable=True
                )
            return data
        raise self._http_error(resp)

    async def check_connectivity(self) -> None:
        """仅发送无凭据、无正文的 HEAD 检查服务可达性，不调用推理端点的 POST。"""
        try:
            async with async_network_client(timeout=self._timeout, follow_redirects=False) as client:
                response = await client.head(self._endpoint)
        except httpx.TimeoutException as exc:
            raise JudgmentError("判断服务连接检查超时", cause=ErrorCause.TIMEOUT, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise JudgmentError(f"判断服务连接失败: {exc}", cause=ErrorCause.NETWORK, retryable=True) from exc
        except ValueError as exc:
            raise JudgmentError(f"判断服务网络配置无效: {exc}", cause=ErrorCause.CONFIG, retryable=False) from exc
        # 认证/方法限制仍证明 HTTP 服务已响应，网关和过载错误不能作为恢复依据。
        if response.status_code == 429 or response.status_code >= 500:
            raise JudgmentError(f"判断服务暂不可达 (HTTP {response.status_code})", cause=ErrorCause.NETWORK, retryable=True)

    @staticmethod
    def _http_error(resp: httpx.Response) -> JudgmentError:
        """按状态码归因：401/403 凭据、422 参数、429/529 与 5xx 可重试。"""
        detail = ""
        try:
            body = resp.json()
            detail = str(body.get("detail") or body.get("error") or body)[:200] if isinstance(body, dict) else str(body)[:200]
        except ValueError:
            detail = resp.text[:200]
        if resp.status_code in (401, 403):
            return JudgmentError(
                f"System One 凭据无效 ({detail})", cause=ErrorCause.CONFIG, retryable=False
            )
        if resp.status_code in (400, 422):
            return JudgmentError(
                f"System One 请求校验失败 ({detail})", cause=ErrorCause.PARAM, retryable=False
            )
        if resp.status_code in (429, 529) or resp.status_code >= 500:
            return JudgmentError(
                f"System One 服务暂不可用 (HTTP {resp.status_code})",
                cause=ErrorCause.NETWORK,
                retryable=True,
            )
        return JudgmentError(
            f"System One 请求失败 (HTTP {resp.status_code}: {detail})",
            cause=ErrorCause.INTERNAL,
            retryable=False,
        )

    @staticmethod
    def _parse_response(
        data: Dict[str, Any], questions: Dict[str, Question]
    ) -> tuple[Dict[str, Answer], str, JudgmentUsage]:
        raw_answers = data.get("answers")
        if not isinstance(raw_answers, dict):
            raise JudgmentError(
                "System One 响应缺少 answers", cause=ErrorCause.INTERNAL, retryable=True
            )
        answers: Dict[str, Answer] = {}
        for qid, question in questions.items():
            raw = raw_answers.get(qid)
            if raw is None:
                raise JudgmentError(
                    f"System One 响应缺少答案 '{qid}'", cause=ErrorCause.INTERNAL, retryable=True
                )
            answers[qid] = _parse_answer(question, raw, qid)
        try:
            usage = JudgmentUsage.model_validate(data.get("usage") or {})
        except ValidationError as exc:
            raise JudgmentError("System One 用量结构无效", cause=ErrorCause.INTERNAL, retryable=True) from exc
        return answers, str(data.get("model") or ""), usage


def _parse_answer(question: Question, raw: Any, qid: str) -> Answer:
    """按问题类型校验答案（答案类型必须与问题类型一致，缺字段即契约破坏）。"""
    model: type[ChoiceAnswer] | type[ScoreAnswer] | type[NoulAnswer]
    if isinstance(question, ChoiceQuestion):
        model = ChoiceAnswer
    elif isinstance(question, ScoreQuestion):
        model = ScoreAnswer
    elif isinstance(question, NoulQuestion):
        model = NoulAnswer
    else:  # pragma: no cover - 判别联合已穷尽
        raise JudgmentError(f"未知问题类型: {type(question)}", cause=ErrorCause.INTERNAL, retryable=False)
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise JudgmentError(
            f"答案 '{qid}' 结构无效: {exc.errors()[0].get('msg', '') if exc.errors() else exc}",
            cause=ErrorCause.INTERNAL,
            retryable=True,
        ) from exc
