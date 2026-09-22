"""TypeSafe 原生通道 — System One HTTP API 客户端。

契约（docs.typesafe.ai/api）：POST {base_url}/v1/systemone，Bearer 认证；
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
from core.tool_errors import ErrorCause

_ENDPOINT = "/v1/systemone"


class TypeSafeClient:
    """TypeSafe System One API 客户端（每次调用独立连接，判断为低频调用）。"""

    def __init__(self, *, base_url: str, api_key: str, timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

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
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as client:
                resp = await client.post(_ENDPOINT, json=payload)
        except httpx.TimeoutException as exc:
            raise JudgmentError(
                "TypeSafe 请求超时", cause=ErrorCause.TIMEOUT, retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise JudgmentError(
                f"TypeSafe 网络错误: {exc}", cause=ErrorCause.NETWORK, retryable=True
            ) from exc

        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError as exc:
                raise JudgmentError(
                    "TypeSafe 响应不是合法 JSON", cause=ErrorCause.INTERNAL, retryable=True
                ) from exc
            if not isinstance(data, dict):
                raise JudgmentError(
                    "TypeSafe 响应结构无效", cause=ErrorCause.INTERNAL, retryable=True
                )
            return data
        raise self._http_error(resp)

    @staticmethod
    def _http_error(resp: httpx.Response) -> JudgmentError:
        """按状态码归因：401/403 凭据、422 参数、429/529 与 5xx 可重试。"""
        detail = ""
        try:
            body = resp.json()
            detail = str(body.get("detail") or body.get("error") or body)[:200]
        except ValueError:
            detail = resp.text[:200]
        if resp.status_code in (401, 403):
            return JudgmentError(
                f"TypeSafe 凭据无效 ({detail})", cause=ErrorCause.CONFIG, retryable=False
            )
        if resp.status_code == 422:
            return JudgmentError(
                f"TypeSafe 请求校验失败 ({detail})", cause=ErrorCause.PARAM, retryable=False
            )
        if resp.status_code in (429, 529) or resp.status_code >= 500:
            return JudgmentError(
                f"TypeSafe 服务暂不可用 (HTTP {resp.status_code})",
                cause=ErrorCause.NETWORK,
                retryable=True,
            )
        return JudgmentError(
            f"TypeSafe 请求失败 (HTTP {resp.status_code}: {detail})",
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
                "TypeSafe 响应缺少 answers", cause=ErrorCause.INTERNAL, retryable=True
            )
        answers: Dict[str, Answer] = {}
        for qid, question in questions.items():
            raw = raw_answers.get(qid)
            if raw is None:
                raise JudgmentError(
                    f"TypeSafe 响应缺少答案 '{qid}'", cause=ErrorCause.INTERNAL, retryable=True
                )
            answers[qid] = _parse_answer(question, raw, qid)
        raw_usage = data.get("usage") or {}
        usage = JudgmentUsage(
            input_tokens=int(raw_usage.get("input_tokens") or 0),
            output_tokens=int(raw_usage.get("output_tokens") or 0),
        )
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
