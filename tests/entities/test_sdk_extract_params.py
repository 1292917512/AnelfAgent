"""entities/_sdk._extract_params 单元测试。

复现历史 bug：带 **kwargs 容错参数的工具（如 recognize_image），
schema 中会出现虚假的 "kwargs: string, required=true"，误导 LLM 传参。
"""

from __future__ import annotations

from entities._sdk import _extract_params


def _tool_with_kwargs(image_path: str = "", prompt: str = "", **kwargs: str) -> str:
    """示例工具。

    Args:
        image_path: 图片路径
        prompt: 提示词
    """
    return ""


def _tool_with_args(first: str, *args: str, flag: bool = False) -> str:
    """示例工具。

    Args:
        first: 首个参数
        flag: 开关
    """
    return ""


def test_extract_params_skips_var_keyword() -> None:
    params = _extract_params(_tool_with_kwargs)
    names = [p.name for p in params]
    assert names == ["image_path", "prompt"]
    assert all(not p.required for p in params)


def test_extract_params_skips_var_positional() -> None:
    params = _extract_params(_tool_with_args)
    names = [p.name for p in params]
    assert names == ["first", "flag"]
    assert params[0].required is True
    assert params[1].required is False
