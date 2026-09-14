"""检索核心能力 — 联网检索、网页读取、仓库文档、HTTP 请求、文件下载、文档重排序。

划分纪律（核心由 Agent 集成，平台组件可插拔）：
- providers/：能力 × 提供者矩阵（Provider 抽象 + SearchCap/ReaderCap/RepoCap
  统一 Protocol）——builtin（本地直连）与 bigmodel（智谱）为内置提供者，
  第三方平台组件经 entities._sdk.register_retrieval_provider 注册；
- fetcher.py / extractor.py / robots.py：直连抓取设施（SSRF 防护 / robots
  合规 / 三层正文提取管线）；
- rerank.py：文档重排序（rerank 类型模型链，内部模型利用）;
- tools.py：AI 工具组（deferred，bootstrap 激活，主用工具常驻）；
- migrate.py：旧实体配置的一次性导入（幂等）。
"""

from core.config import register_configs_safe

# 核心配置项：分组名 retrieval，配置中心与检索页签自动可见
register_configs_safe({
    "retrieval": {
        "retrieval_proxy": {
            "description": "网页抓取代理地址（空=不使用代理；仅直连抓取生效）",
            "default": "",
        },
        "retrieval_active": {
            "description": "能力 × 固定提供者选择（JSON 字典，如 {\"search\": \"bigmodel\"}；缺省 auto 自动选择）",
            "default": {},
            "value_type": "json",
            "advanced": True,
        },
        "retrieval_disabled_providers": {
            "description": "停用的检索提供者（JSON 数组；停用后不参与自动选择）",
            "default": [],
            "value_type": "json",
            "advanced": True,
        },
        "retrieval_bigmodel_api_key": {
            "description": "智谱 BigModel Coding Plan API Key（检索/网页读取/仓库文档）",
            "default": "",
            "value_type": "password",
        },
        "retrieval_ssrf_protection": {
            "description": "是否开启 SSRF 防护（拒绝访问回环/内网/链路本地等受限地址）",
            "default": True,
        },
    },
})
