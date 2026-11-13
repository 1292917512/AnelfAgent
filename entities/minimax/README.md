# MiniMax 组件

组件通过 `entities._sdk` 注册视觉、声音、检索和流式 TTS 能力；图片协议实现在
`image_adapter.py`，模型管理与组件直连共用同一实现。核心不导入 MiniMax 组件。
实体自动发现时注册协议，热重载同名覆盖，卸载时移除协议及主机匹配规则。

AI 通过 `vision_config(action="providers")` 读取 MiniMax 提供者的
`details.image_gen.setup_guide`。指南按需返回连接、凭据、模型类型、超时、优先级、扩展参数与调用示例；
编辑能力引用同一份说明，不新增专属工具 schema 或常驻工具目录项。组件卸载时说明随提供者一起移除。
配置仍使用通用模型工具：
`list_models(model_name=...)` 回读，`update_model_config` 修改 `timeout`、`model_types`、
`extra_body` 等，`update_provider` 修改连接；数组/对象以 JSON 字符串整体替换，先读后改。

## 图片模型接入

推荐在模型管理中配置供应商与模型，凭据、代理、请求头和超时由模型配置统一管理：

```json
{
  "id": "minimax-images",
  "name": "MiniMax Images",
  "base_url": "https://api.minimax.cn/v1",
  "api_key": "${MINIMAX_API_KEY}",
  "api_type": "openai",
  "media_protocol": "minimax",
  "models": [
    {
      "id": "minimax-h3-image",
      "model": "MiniMax-H3-Image",
      "model_types": ["image_gen", "image_edit"],
      "timeout": 650,
      "extra_body": {"output_format": "webp"}
    }
  ]
}
```

将模型加入图片生成与编辑的优先级链，工具选择 `provider="models"` 或使用默认能力链。
`minimax.cn`、`minimaxi.com`、`minimax.io` 可自动匹配；自定义网关须显式选择媒体协议
`minimax`。`MiniMax-H3-Image-Highspeed` 使用相同协议；`image-01` / `image-01-live`
继续使用 `/v1/image_generation` 和单张 `subject_reference`。

组件直连使用 `provider="minimax"`，密钥来自组件凭据中心。配置中心 `entity/minimax` 的
`minimax_image_model`、`minimax_image_base_url`、`minimax_image_timeout` 控制此路径；模型名
留空时兼容 `config.json` 的 `default_image_model`（缺省 `image-01`）。这些选项不覆盖模型管理配置。

## 图片调用

```python
await generate_image(
    prompt="一只橘猫在窗台上", image_size="auto", n=2,
    options={"aspect_ratio": "16:9", "area": "2K", "output_format": "webp"},
)
await edit_image(
    prompt="把图1的主体放进图2的场景，保持图3的配色",
    image_paths=["workspace/cat.png", "https://example.com/scene.jpg", "mm_file://123"],
    options={"stream": True},
)
```

`generate_image.reference_images` 和 `edit_image.image_paths` 接受有序图片列表。旧的
`reference_image` / `image_path` 仍有效，与列表同时提供时排在最前面。本地路径在工具入口
执行沙箱检查，再异步转成 Data URL；HTTP(S)、Data URL 与 `mm_file://` 引用按顺序直传。
H3 最多 16 张参考图、一次最多生成 10 张；旧协议只支持一张主体参考图、最多生成 9 张。

H3 支持像素尺寸、`auto` 或比例。指定像素尺寸时不能同时传 `aspect_ratio` / `area`。
扩展参数通过模型 `extra_body` 提供默认值，调用 `options` 逐项覆盖，支持
`aspect_ratio`、`area`、`output_format`、`response_format`、`output_compression`、
`aigc_watermark`、`disable_prompt_enhanced`、`stream`；编辑始终开启提示词增强。
`b64_json` 按输出格式保留 MIME 类型，不支持流式。输入尺寸和格式的最终校验由服务端负责。

SSE 由通用传输层解码，组件按 `image_index` 还原结果顺序，工具在结束后统一落盘。
`image_details` 保留每张图片的尺寸、增强提示词或错误；`usage` 与 `request_id` 保留供应商
报告值，不加入主对话用量。返回成功表示至少有一张产物，调用方应检查逐图错误。

图片请求的总时限与 HTTP 超时遵循所选模型 `timeout`；直连组件使用自身配置。工具不再
叠加固定 300 秒限制，显式 `_timeout` 与用户取消仍生效。H3 服务端最长等待 600 秒，建议
配置至少 600 秒。请求不具备幂等保障；超时、断流或结果无法解析时返回未知结果，停止自动
模型/提供者回退，避免重复生成与计费。已有明确失败响应仍遵循模型切换策略。

## 维护与验证

新旧协议差异只在组件内实现，通用参数、传输、结果保存留在核心。图片协议的自动注册与
回滚复用现有模块注册事务；不要在核心增加 MiniMax 主机或模型名判断。

运行 `uv run pytest entities/minimax/tests tests/unit/agent/llm/test_media_client.py` 验证
协议、组件注册、参数边界、单图/多图和 SSE。离线 HTTP 替身测试不等同于真实图片生成验收。

协议依据：[MiniMax 图片生成](https://platform.minimax.cn/docs/guides/image-generation)。
