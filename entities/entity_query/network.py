"""AI 网络诊断工具。

模型看到按需工具结果中的规则来源、实际路径、HTTP 状态与采样耗时。
默认三组各三次采样，约千级 token；不注入常驻上下文，不改缓存前缀。
"""

from typing import Literal

from pydantic import ValidationError

from entities._sdk import ErrorCause, NetworkProbeRequest, diagnose_network, error_from_exception, tool, tool_error


@tool(group="entity", tags=["core", "network"], concurrency_safe=True, timeout=60)
async def check_network_proxy(
    url: str = "https://www.gstatic.com/generate_204",
    mode: Literal["compare", "system", "direct", "proxy"] = "compare",
    samples: int = 3,
    timeout_seconds: float = 5,
) -> str:
    """检测服务端当前代理与站点规则的连接质量，结果仅代表本次采样。

    Args:
        url: HTTP(S) 检测地址，不含凭据或查询参数。仅 HEAD，不跟随跳转。
        mode: compare 对比规则/直连/代理；其余模式仅检测该路径。
        samples: 每条路径独立连接采样次数，1 至 5。
        timeout_seconds: 单次完整请求截止秒数，1 至 10。
    """
    try:
        request = NetworkProbeRequest(url=url, mode=mode, samples=samples, timeout_seconds=timeout_seconds)
        return (await diagnose_network(request)).model_dump_json(exclude_none=True)
    except ValidationError:
        return tool_error("检测参数无效：URL 不得含凭据或查询参数；samples=1..5，timeout_seconds=1..10",
                          cause=ErrorCause.PARAM, retryable=False)
    except Exception as exc:
        return error_from_exception(exc, action="检测网络代理")
