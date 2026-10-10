# 视觉与能力路由

视觉源框架、视频理解链路、能力提供者路由框架与检索核心域的现状说明。修改 `agent/vision/`、
`agent/capabilities.py`、`agent/retrieval/` 或平台组件包时按需阅读。

## 视觉源框架（agent/vision/）

- **framework**（VisualSource 基类 + 注册表）：来源组件化——`entities/screen`（mss 截屏）等经
  `_sdk.register_vision_source` 接入，外部帧经 POST /api/vision/push 汇入统一缓冲
- **buffer**（分源判变）/ **watcher**（轮询循环）/ **capture**（帧契约 + 分块 dHash）/ **context**
  （变化驱动注入 + 注入轨迹）：视觉状态行与变化帧经 vision provider 注入（vision_context_inject
  可关），变化帧仅变化轮携带
- **工具**（vision 组，deferred bootstrap 激活）：vision_look/watch/sources + 生成系列
  （recognize_image/recognize_video/generate_image/edit_image/generate_video 常驻 always + 任务管理
  与 vision_config 配置工具 core）
- **视觉源激活体系**（`framework.py` disabled_sources/set_enabled + `vision_source_set` 工具）：
  持久化启停（vision_disabled_sources 配置，热读取）——停用即 watcher 拒启/vision_look 拒取/
  上下文注入排除/外部推送拒收；AI 与 Web 页签同源开关
- **生成能力组件**经 `_sdk.register_visual_provider` 接入

## 视频理解链路

- **supports_video 能力声明**（`LLMClientConfig.supports_video` + `vision/capabilities._run_video_understand`
  严格候选过滤）：视频识别链**仅投送声明 supports_video 的视觉模型**——整段视频 base64 体积大且
  多数视觉端点不接受 video block，未声明即视为不支持，无声明模型时报配置缺失引导显式开启（不做
  全链喷洒试错）。声明入口三处同源：llm_clients.json / Web 模型编辑器「视频理解」开关 / AI
  update_model_config
- **describe_video 直发 HTTP**（`llm_client.describe_video`）：不经过对话协议层与 litellm——两侧
  转换层都不认识 video block（litellm Anthropic 转换层校验拒绝；Responses 转换层无 video 映射）。
  anthropic 端点原生 video block（/v1/messages），其余端点 OpenAI 兼容 video_url block，端点不支持
  即以 HTTP 错误显式暴露供候选链回退。MiniMax 视频理解仅官方 Anthropic 兼容端点（须挂独立
  anthropic 供应商条目）

## 富媒体上下文注入

- **ProviderSnapshot 携带 `media: List[ContextMedia]`**（core 中立表达，kind=image/audio/video）；
  组装点按 kind 分派（协议物理约束）：clip 文本走 system 消息；image 在视觉模型下集中组 user
  角色多模态消息（image block 仅 user 角色可靠；等距抽样 ≤3 张保头中尾；图在文前），非视觉降级
  为标签；**audio/video 一律降级为标签并入 clip 文本**（litellm 对话转换层不接受这两类 block），
  AI 经既有媒体工具链处理——不静默丢媒体。位置在工具链之后、exec_context 之前，不触碰前缀
- **ai_desktop 媒体契约**（`entities/ai_desktop/framework.py`）：DesktopModule 的
  `render_media() -> List[ContextMedia]`（默认空）；文本必须自足（降级路径只文本+标签生效）

## 能力路由框架（agent/capabilities.py）

CapabilityProvider 协议（name/capabilities/is_configured/run）+ CapabilityRouter（注册表 +
配置化优先级链 + 失败降级 + 错误聚合归因）。链语义：配置键（JSON 字典 {能力: [提供者]}）显式给出
非空链时严格按配置；否则默认链 + 声明该能力的已注册组件自动入链（即插即用）。

- 内部模型利用 = 各领域内置 `models` 提供者桥接 llm_clients.json 对应类型模型优先级链
- 第三方组件经 `_sdk` 注册桥接入同一路由
- **领域分层**：声音/视觉域只提炼能力接口，具体平台由组件实现并登记凭据（见
  [运维分册·组件凭据中心](operations.md)）；无组件/无凭据时链自动降级
- **视觉能力域**：understand（图片/视频理解，视频仅投送 supports_video 声明模型）/ image_gen /
  image_edit / video（生成+任务管理）；配置键 vision_provider_priority / vision_default_* /
  vision_style_presets（vision 组）
- **声音能力域**：tts（一次性合成，实时流式走 agent.tts 注册表互补）/ voice_mgmt / music
  （详见[声纹音频分册](audio-voiceprint.md)）
- **工作区路径统一**（`agent/utils/workspace.py` + `agent.approval.policy.workspace_paths_port`）：
  核心层工具的路径解析/沙箱校验/产物落盘（uploads）统一入口；沙箱准入经端口由 entities.filesystem
  施绑（sandbox_enabled + check_sandbox 组合判定），未施绑时仅接受绝对路径
- 旧媒体库/网络工具配置的残留迁移由 `agent/runtime/config_migrate.py`（bootstrap
  `migrate_legacy_configs` 节点）一次性导入，幂等

## 检索核心域（agent/retrieval/）

联网检索/网页读取/仓库文档/HTTP 请求/文件下载/文档重排序核心化：

- **providers**（能力×提供者矩阵：Provider ABC + SearchCap/ReaderCap/RepoCap Protocol；builtin
  本地直连/bigmodel 内置，组件经 `_sdk.register_retrieval_provider` 接入，可启停）
- **fetcher**（SSRF 防护直连抓取）+ **extractor**（三层正文提取）+ **robots** + **rerank**
  （rerank 模型链）
- **工具组 retrieval**：web_search/web_fetch/repo_docs/web_request/extract_page_links/web_download
  常驻 always + retrieval_providers/rerank_search core；web_download 注册 timeout=300（AI 参数
  在此范围内生效）
- 配置统一走 ConfigManager（retrieval_proxy/retrieval_active/retrieval_disabled_providers/
  retrieval_bigmodel_api_key(password)/retrieval_ssrf_protection，retrieval 组）
- **数据面**：`services/retrieval.py` + `/api/retrieval/*` + 视觉/声音能力状态端点；检索页
  （/retrieval）能力×提供者矩阵 + 抓取设置；视觉页「生成能力」页签 + 声音页「生成」页签共用
  `components/common/CapabilityChainPanel.tsx`
