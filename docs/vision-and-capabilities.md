# 视觉与能力路由

视觉源框架、视频理解链路、能力提供者路由框架与检索核心域的现状说明。修改 `agent/vision/`、
`agent/capabilities.py`、`agent/retrieval/` 或平台组件包时按需阅读。

## 视觉源框架（agent/vision/）

- **framework**（VisualSource 基类 + 注册表）：来源组件化——`entities/screen`（mss 截屏）等经
  `_sdk.register_vision_source` 接入，外部帧经 POST /api/vision/push 汇入统一缓冲
- **buffer**（分源判变）/ **watcher**（轮询循环）/ **capture**（帧契约 + 分块 dHash）/ **context**
  （变化驱动注入 + 注入轨迹）：视觉状态行与变化帧经 vision provider 注入（vision_context_inject
  可关），按会话与执行请求独立记录已见帧。首次读取或内容变化时最多补充三个源的画面，
  后续轮次补齐尚未呈现的源；同一请求不重发静态帧。轨迹仅存内存，最多保留 200 个请求。
  外部推送的活跃时间看最新接收帧，静态画面持续推送时不会因未变化而被误判离线
- **工具**（vision 组，deferred bootstrap 激活）：vision_look/watch/sources + 生成系列
  （recognize_image/recognize_video/generate_image/edit_image/generate_video 常驻 always + 任务管理
  与 vision_config 配置工具 core）
- **视觉源激活体系**（`framework.py` disabled_sources/set_enabled + `vision_source_set` 工具）：
  持久化启停（vision_disabled_sources 配置，热读取）——停用即 watcher 拒启/vision_look 拒取/
  上下文注入排除/外部推送拒收；AI 与 Web 页签同源开关
- **生成能力组件**经 `_sdk.register_visual_provider` 接入

## 非视觉模型的图片识别

`recognize_image` 将图片与调用方的分析要求交给模型 SDK，结果通过工具回执进入主模型上下文。
识图调用不继承整段会话；`prompt` 应说明识别目标和必要背景。本地路径、图片 Data URL 和远程
URL 共用异步准备流程，读取与压缩放工作线程，同次调用的模型回退复用准备结果。支持 Base64
的模型优先接收下载后的图片，仅支持 URL 的模型直接接收原链接；本地图片不会发送给 URL-only 模型。

配置中心 `model/describe` 的 `describe_image_reasoning_effort` 控制辅助识图思考档位，默认 `off`，
空值跟随模型配置；复杂分析可提高档位，SDK 调用可用 `options` 按次覆盖。它不影响主对话的原生
视觉请求。`describe_image_first_response_timeout` 默认 30 秒，限制等待首个有效思考/正文增量；
0 表示只使用模型超时。输出开始后遵循模型空闲超时，不因超过首响应时限中断正常输出。
空结果、输出截断和缺失结束标记均按失败回退；日志分别记录图片准备、首响应及模型完成耗时。

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

状态接口区分 `defaults`（自动发现）、`overrides`（显式配置）与 `chains`（生效链）。
界面只回写覆盖项，修改一种能力不会冻结其他能力的自动发现；恢复自动即删除该覆盖项。
提供者返回失败字典与抛异常同样属于失败，不能被路由器包装成成功。显式指定的提供者失败时
返回原有诊断；结果声明 `outcome=unknown` 时保留任务编号并停止回退，避免重复创建远端任务。

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
  本机抓取/bigmodel 内置，组件经 `_sdk.register_retrieval_provider` 接入，可启停）
- **fetcher**（SSRF 防护与规则路由抓取）+ **extractor**（三层正文提取）+ **robots** + **rerank**
  （rerank 模型链）
- **工具组 retrieval**：web_search/web_fetch/repo_docs/web_request/extract_page_links/web_download
  常驻 always + retrieval_providers/rerank_search core；web_download 注册 timeout=300（AI 参数
  在此范围内生效）
- 配置统一走 ConfigManager（retrieval_active/retrieval_disabled_providers/
  retrieval_bigmodel_api_key(password)/retrieval_ssrf_protection，retrieval 组）
- 网络代理统一使用 [核心网络 SDK](network.md)，工具通过 `proxy_mode` 选择全局规则、直连或全局代理。
- **数据面**：`services/retrieval.py` + `/api/retrieval/*` + 视觉/声音能力状态端点；检索页
  （/retrieval）能力×提供者矩阵 + 抓取设置；视觉页「生成能力」页签 + 声音页「生成」页签共用
  `components/common/CapabilityChainPanel.tsx`

视觉预览按来源与帧时间生成资源 URL，停用源不参与预览。视觉配置沿用配置元数据控件，
字符串、JSON、枚举与数值保持声明类型；声音和视觉子页签按需加载。检索连通测试检查提供者
返回的语义错误，同时返回能力、提供者及耗时，HTTP 请求成功不等于检索成功。
