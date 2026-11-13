"""MiniMax 图片接入说明，由视觉提供者状态查询按需返回。

模型可见：H3/旧协议配置步骤、参数和错误处理约束，仅进入查询工具结果。
Token：查询时返回约千 token 的说明，不新增工具 schema 或常驻目录项。
缓存：不注入稳定前缀；组件注册、卸载沿用现有能力路由。
"""
from __future__ import annotations

from typing import Any


def image_setup_guide() -> dict[str, Any]:
    """返回图片接入说明；不注册工具、不读写配置、不发起网络请求。"""
    return {
        "recommended_path": "模型管理接入，生成/编辑时 provider=models；先 list_models 检查现有配置，匹配时复用供应商",
        "credentials": "需要 MiniMax 平台按量 API Key；Coding Plan 订阅密钥不能替代。使用已配置凭据或用户提供的环境变量引用，不能使用掩码值",
        "setup": [
            {"tool": "add_provider", "arguments": {
                "provider_id": "minimax-images", "base_url": "https://api.minimax.cn/v1",
                "api_type": "openai", "media_protocol": "minimax", "api_key": "${MINIMAX_API_KEY}",
            }},
            {"tool": "add_model", "arguments": {
                "provider_id": "minimax-images", "model_id": "minimax-h3-image", "model": "MiniMax-H3-Image",
                "model_types": "image_gen,image_edit", "supports_tools": False, "timeout": 650,
                "extra_body": {"output_format": "webp"},
            }},
        ],
        "routing": "分别 get_model_priority(model_type=image_gen/image_edit)，按用户意图用 set_model_priority 调整顺序；model_ids 为逗号分隔配置 ID，保留需要的原候选；无需 switch_model 改变对话模型",
        "update": {
            "read": {"tool": "list_models", "arguments": {"model_name": "minimax-h3-image"}},
            "write": {"tool": "update_model_config", "arguments": {
                "model_name": "minimax-h3-image", "field": "extra_body", "value": '{"output_format":"png"}',
            }},
            "fields": "timeout 为秒；model 为远端模型名；model_types 为 JSON 数组字符串；extra_body 为 JSON 对象字符串，整体替换，{} 清空。先读后改、更新后回读；热生效且持久化",
            "connection": "地址/凭据/代理/media_protocol 使用 update_provider；会影响该供应商下所有模型。凭据管理沿用现有权限审批",
        },
        "examples": {
            "generate_image": {"prompt": "一只橘猫在窗台晒太阳", "provider": "models", "image_size": "auto",
                               "n": 1, "options": {"aspect_ratio": "16:9", "area": "1K"}},
            "edit_image": {"prompt": "把图1主体放进图2的场景", "provider": "models",
                           "image_paths": ["workspace/subject.png", "workspace/scene.png"],
                           "image_size": "auto", "options": {"stream": True}},
        },
        "constraints": {
            "models": ["MiniMax-H3-Image", "MiniMax-H3-Image-Highspeed"],
            "limits": "H3 n=1..10，参考图 1..16 张，顺序对应图号；参考图可为工作区文件、HTTP(S)、Data URL、mm_file://",
            "size": "像素宽x高，或 auto 配合 aspect_ratio/area；像素尺寸不能同时传比例/面积。使用比例/面积选项时显式传 image_size=auto，避免默认像素尺寸冲突",
            "options": ["aspect_ratio", "area", "output_format", "response_format", "output_compression",
                        "aigc_watermark", "disable_prompt_enhanced", "stream"],
            "formats": "jpeg/png/webp；response_format=url/b64_json；b64_json 不支持 stream；编辑不接受 disable_prompt_enhanced",
            "results": "查看 file_paths、image_details 和 usage；部分成功时仍需检查逐图错误。outcome=unknown/retryable=false 时先核查远端结果，勿自动重试",
            "timeout": "遵循模型 timeout；H3 服务端最长等待 600 秒，示例配置 650 秒。工具显式 _timeout 可缩短时限",
        },
        "legacy": "image-01/image-01-live 自动使用旧 /v1/image_generation；最多 9 张产物、1 张参考图；原 reference_image/image_path 参数仍有效，与多图参数同时传时单图排首位",
        "component_direct": "provider=minimax 使用组件凭据，独立于模型管理。list_provider_keys(domain=vision)/set_provider_key 管理 minimax.api_key；get_entity_config(entity_name=entity/minimax) 读取配置，update_entity_config 更新 minimax_image_model/minimax_image_base_url/minimax_image_timeout；模型名留空沿用旧默认 image-01",
        "verify": "配置后先回读、vision_config(action=providers) 确认状态；首次生成最小 n=1 的请求检查落盘，真实调用会按平台计费",
        "documentation": "https://platform.minimax.cn/docs/guides/image-generation",
    }
