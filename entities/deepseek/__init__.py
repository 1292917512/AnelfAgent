"""DeepSeek 实体 — DeepSeek Search/Fetch 接入检索核心域的组件包。

检索组件（联网检索 + 网页读取）→ 检索提供者矩阵：retrieval 组的
web_search / web_fetch 工具经能力路由分派到本组件。凭据经组件凭据中心
（provider_keys.json 的 deepseek 条目，Web/AI/文件三面可配），
llm_clients.json 的 deepseek.com 供应商凭据回退，DEEPSEEK_API_KEY 环境变量
兜底；删除本目录即整体拔出（检索矩阵的注册随之消失）。
"""

from entities._sdk import entity, entity_manifest, register_retrieval_provider

entity("deepseek", "DeepSeek - Search/Fetch 联网检索与网页读取（Markdown，按成功请求计费）的检索组件包")

entity_manifest(
    display_name="DeepSeek",
    icon="globe",
    description="DeepSeek Search/Fetch 组件：联网检索（结构化引用）+ 网页读取（Markdown 正文）",
    version="1.0.0",
    order=33,
    group="deepseek",
)


def register_components() -> None:
    """向检索矩阵注册 DeepSeek 组件（实体导入时调用；同名覆盖幂等）。"""
    from .providers import DeepSeekSearchProvider

    register_retrieval_provider(DeepSeekSearchProvider())


register_components()

# 组件凭据登记（检索页的组件凭据面板与 AI 工具据此展示）
from entities._sdk import CredentialField, register_provider_key  # noqa: E402

register_provider_key(
    "deepseek", domain="retrieval", title="DeepSeek",
    fields=(CredentialField("api_key", "API Key"),), owner_title="DeepSeek",
    destination="/entities/deepseek",
    description="Search/Fetch 检索接口的 API Key（按成功请求计费 $0.75/千次；"
                "留空时回退 llm_clients.json 的 deepseek 供应商凭据）",
)
