# 实时语音

实时传输枢纽、语音会话、全双工实时引擎、流式 ASR 与 TTS 管线的现状说明。修改 `core/realtime_hub.py`、
`agent/voice/`、`agent/realtime/`、`agent/tts/` 或 WS 语音数据面时按需阅读；声纹与音频库见
[声纹音频分册](audio-voiceprint.md)。

## 传输层

- **统一实时枢纽**（`core/realtime_hub.py`）：SSE（`web/routers/chat.py:/stream`）与 WebSocket
  （`web/routers/chat_ws.py:/chat/ws`）共享同一订阅/分发面。订阅者带身份（connection_id/
  client_kind=web|desktop）与 topic 过滤；帧按类型分级背压——增量帧队列满丢旧保新，终态帧
  （reply/media/turn_end/ui_command/approval_request/share）不可静默丢，腾不出位即判死订阅者
  （客户端重连 + /history 重同步）
- **WS envelope 协议**：上行文本帧 JSON `{"action", "request_id"?}`，二进制帧仅承载麦克风 PCM；
  下行 `{"type", ...}` 数据帧 + `{"type":"status"}` 结构化错误帧（错误码封闭集见模块头）。
  action：ping/send_message/interrupt/ui_answer/ui_state_report/voice_start/voice_end + 音频会话别名。
  client=desktop 单槽位「最新连接赢」（CONNECTION_SUPERSEDED 踢旧防双写），client=web 多开不踢；
  鉴权 cookie `_anelf_token` 或 `?token=`（WS 不经 BaseHTTPMiddleware，自校验）
- **二进制音频帧**（`core/audio_frames.py`）：magic 4 字节 + LE uint32 采样率 + PCM16，采样率白名单
  {16000,24000,48000}，单帧 120ms 漂移门；坏帧只丢帧不关连接。Web 实时语音与桌面壳共用同一帧格式
- **WS 语音数据面**：voice_start 增加 mode=realtime——sink 挂本连接订阅队列（JSON 事件走下行泵，
  音频帧以 __audio__ 标记转二进制 PCM 帧直发，满时丢最旧保最新）；voice_end/断连统一收尾（实时优先）；
  **麦克风单会话纪律**（成段录音与实时通话互斥，双向 VoiceLeaseBusy）

## 语音会话（agent/voice/）

独立可插拔模块：MicLease 式租约（同 owner 同时一条语音连接，冲突显式拒绝）+ 输入预处理链 +
端点检测 + 看门狗兜底收束 + 成段写 WAV（workspace/uploads/voice/）。会话开启时绑定 VoiceDelivery，
成段随 utterance 交 `voice_sink_port`（LateBinding 端口，组合根施绑 `deliver_utterance`）。
成段广播 EVENT_VOICE_UTTERANCE。配置组 `voice` 热读取。

- **输入预处理链**（`agent/voice/preprocess.py`）：谱减降噪 → 自动增益 → 限幅：流式 STFT
  （sqrt-Hann 50% 重叠 OLA）纯 numpy 零依赖。降噪静音帧学稳态噪声谱（连续帧确认防开头语音误学）、
  语音帧超减抑制；AGC 语音帧向目标响度、静音帧保持增益（不放大底噪）；限幅峰值软顶。
  voice_denoise / voice_agc 独立开关，全关字节直通零开销；flush() 收尾冲刷
- **端点检测**（`agent/voice/turn_detection.py`）：TurnDetector 协议 + 梯队实现（voice_turn_detector
  默认 auto：smart_turn→silero→energy 缺失自动降级）：
  - smart_turn = 语义端点：基座检测器触发 SPEECH_END 后进入候选等待，对尾部 8 秒音频本地推理
    「说完概率」（SmartTurn ONNX，Whisper log-mel 特征纯 numpy 提取），≥ threshold 才收束，否则
    复评、硬上限兜底（绝不挂死）；候选期用户重新开口即取消；模型缺失或连续 3 次推理失败熔断退化为
    纯 VAD。**推理离泵**：语义裁决经专用单线程池异步提交/后续帧收割——同步推理挂在 accept_pcm 上
    会卡麦克风帧泵
  - silero = v5/v6 模型级 VAD；energy = 零依赖兜底（`core/audio_frames.py::EnergyVad`：滑窗 10 分位
    数噪声地板 + 上行限速 + 迟滞双门限 + onset 连续帧确认 + DC 阻断）
  - 模型经本地模型资产解析（workspace/models/，见[运维分册](operations.md)）；模型加载失败冷却 60s
    允许重试
- **语音接入统一入口**（`agent/voice/deliver.py` + `services/voice.py`）：deliver_utterance 把语音段
  作为 VOICE MessageSegment 经 **AgentApp.send_message 统一入口**（→ Everything → pipeline → Mind）
  投递，与频道语音消息完全同路径，不发明第二条语音路径；services/voice 是 web 层纯门面

## 实时引擎（agent/realtime/）

全双工语音对话：会话状态机 LISTENING/THINKING/SPEAKING + 轮次令牌（turn_id 贯穿 ASR 定稿→思维→
TTS→播放帧）+ 租约。播放链 `PcmResampler` 边界连续重采样（相位/尾样本跨块延续）+ 有界
PlaybackQueue（满时丢最旧音频帧；audio_chunk/audio_done 帧对，final 帧永不被挤掉）。

### 级联管线（默认模式）

麦克风帧 → 端点检测 → 流式 ASR 定稿 → **AgentApp 统一入口**进思维（人格/记忆/工具全量生效，
与文字消息同一条大脑路径）→ event_bus 回复增量（scope 匹配仲裁，barge-in 后旧轮增量不再送 TTS）→
TTS 管线 → 下行音频帧；回复文本照常走频道事件流（语音只是第二种呈现）。

### 原生三方言（realtime_mode=native）

`agent/realtime/native/`（base/openai/gemini/qwen）：OpenAI Realtime、Gemini Live 与千问实时语音
（`wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen-audio-3.0-realtime-plus`，Bearer 鉴权，
pcm 双端 16k 入/24k 出，凭据走组件凭据中心 dashscope 条目——遵守「实时语音凭据不经大模型配置」
约定，与 openai/gemini 从 llm_clients 取凭据不同源）。提供方 speech_started 事件驱动引擎打断；
系统指令缺省从人格档案组装（realtime_native_instructions 可覆盖）；native 为最低延迟形态但
人格/记忆不经思维链，由会话指令注入人格简述。引擎零改动——方言实现 NativeRealtimeClient 协议
即插即用。千问方言错误分级：invalid_request_error 不断连（DEBUG 忽略），server_error 才收线

### 仲裁与收束

| 机制 | 位置 | 说明 |
|------|------|------|
| 播报车道 | `agent/realtime/arbiter.py`（SpeakLane/Utterance）+ `RealtimeSession.lane` | 会话内全部 TTS 播报的串行化、优先级与归因：①单工车道——同一时刻至多一个「生产中」单元写播放队列，多来源绝不交错；②优先级抢占——回复抢占在播主动消息（取消其生产并清空未播音频，回复即时开声），主动消息绝不抢回复、彼此 FIFO 全播；③归因收尾——单元持单调 uid 逐块自检，被取代者不写帧/不迁状态/不重复 audio_done |
| 回复完成归因 | `reply_finalize.py`（EVENT_AFTER_REPLY 带 turn_id）+ `engine._on_after_reply/_settle_reply` | 完成事件按 mind turn 归因结算：对得上立即收尾语音流；归因不上宽限观察（1.5s，新增量到达即取消）后兜底结算——绝不因归因失败让回复「说不停」，也不误杀新一轮语音流 |
| 语音收束离线化 | `engine._spawn_finalize` + `session.finalize_task` | ASR 定稿/声纹识别/入轮在后台任务执行，麦克风帧流不被定稿阻塞（收束期间到达的新语音进新一轮）；先就地摘下 ASR 会话并快照缓冲再走网络调用；收束任务串行链防两段语音乱序 |
| barge-in 回声防护 | `engine._arm_barge_in_check` | 播放/思考中听到的语音不立即打断：先持续观察 realtime_barge_in_onset_ms（默认 250ms），仍是语音才确认打断；确认窗内收束的短促碎响按回声丢弃——扬声器回声多为碎响，持续开口才是真打断 |
| 静默失败纪律 | `engine.py` | 一切失败必须下行成事件：无 ASR 启动拒绝、无 TTS 降级警告、空转写收帧、思维投递失败、回复出错、原生通道错误、TTS 零产出；纯工具轮 after_reply 后状态必回收 LISTENING；TTS 句级超时防提供者挂起；客户端 start 等 voice_ack 握手 |
| 通话路由补全 | `engine.session_for_scope`（基座匹配，#session 后缀不阻断）+ voice_spoken 移交引擎 | 多会话通话中主动消息不静默不播；voice_spoken 在实际播出完成时广播。轮末纯文本不在 deliver_text 重复路由——级联模式下回复增量已流入 TTS 车道（语音出口唯二：增量流 / send_message 自动路由） |
| 会话续命 | `engine._reattach` + `_by_user` 身份索引 + `realtime_reconnect_grace_seconds`（默认 5s） | 同一用户重新 voice_start 时接续既有会话而非重建：轮次令牌、端点检测与预处理状态、播放队列、车道与挂起回复全保留（chat_id 变化时挂起回复随迁）。断开后会话保留等重挂，超窗自动收线；宽限任务按用户身份去重。客户端意外断连按 400ms×次数 退避重连三次，显式挂断不重连 |

## 流式 ASR（agent/audio/streaming.py + entities/audiosync/）

StreamingAsrSession 协议（accept_pcm → partial/final 事件 + commit 定稿 + close 收口）。
链优先级：Qwen 实时转写（裸 WS，manual 提交=本地端点单一裁决）→ FunASR 滚动窗（仅展示辅助）→
无流式提供者时引擎退化为整段缓冲转写。关键纪律：

- **一轮语音一条连接**（会话随轮生灭、commit 后即关——跨轮共享连接会让新一轮首帧混入上轮待
  commit 缓冲，造成定稿串字）；凭据按端点归属解析（llm_clients 同 host 供应商 → 组件凭据 →
  环境变量，经 _sdk 桥不越层），显式绕过环境代理
- **帧泵零阻塞**：引擎 `_feed_asr` 只入队，网络转写在每会话消费任务中执行（同步转写会致泵上
  永久积压）；commit 经队列哨兵保序（定稿必在全部已喂帧之后），future 回传有界 20s
- **代际安全**：`_spawn_finalize` 在帧泵内同步摘下本轮 ASR 会话/队列/音频/轮次号，新轮起始必开
  新一代——哨兵跨代会把新代提前定稿、旧代结果串进新轮；ASR 会话开/闭所有权归引擎
- **前滚回补**：收听态未入段帧留 ~300ms 前滚痕，onset 确认开轮时回补进 ASR 与整段缓冲
  （确认吃掉的首音节不丢）

## TTS 管线（agent/tts/）

流式提供者协议（TtsStream=PCM 块流+采样率声明）+ 优先级链运行时降级（首字节前无缝切换、首字节后
截断该句）；`SentenceSplitter` 流式断句（CJK 标点/英文缩写与小数豁免/超长软切/短尾合并，
**首句窗口 6~36 字提前断句**——TTS 首请求不等第一个完整句，开声延迟缩到首个分句）+
`strip_for_speech` 朗读清洗（markdown/旁白剥离、CJK 空格规范化、emoji 剔除）；句级管线预取
（tts_prefetch_sentences）；压缩流经 ffmpeg 管道解码 PCM16。

- 内置提供者：openai 风格（audio/speech 流式）/ edge-tts（MP3→解码，可选库）；组件：minimax HTTP
  （t2a_v2 流式 PCM 直出）/ minimax WS（双工长连接），经 `_sdk.register_tts_provider` 注册同链互备；
  百炼组件（entities/dashscope）见[声纹音频分册](audio-voiceprint.md)
- **抢占清场**：`playback.drop_pending` + `engine._on_delta` 提交回复时——回复抢占在播主动播报时
  清空其未播帧（含收束帧），回复音频紧跟当前已下发帧直落

## 通话与频道能力

- **实时语音是频道能力**（channel capability，`agent/channel/channel_types.py` REALTIME_VOICE +
  `agent/realtime/protocol.py`）：webui 频道第一个声明并实现；对外接入契约单一定义点（WS 控制流 +
  PCM 帧格式 + 下行事件表），外部客户端按协议连 /api/chat/ws 即可接入，能力探测经 /api/adapters
- **通话自动语音路由**（呈现形态归频道）：AI 统一经 send_message 发消息，出口层
  （`output_tools._speak_if_on_call` → `engine.speak_to_scope`）自动路由——通话会话的消息同步 TTS
  播出（她在说→追加队列；思考/空闲→新播报；用户说话中→不插播仅文字送达，结果注记 voice 字段）。
  AI 无需感知通话状态，无专用语音工具（voice 组仅 realtime_status）
- **语音形态消息**：用户语音转写定稿与 AI 语音播出均进频道聊天流（voice_transcript/voice_spoken
  事件 → 前端消息气泡，形态徽标）；对话历史同桶（语音轮与文字轮对记忆/上下文一视同仁）
- **说话人标注**：语音轮次附说话人标注（声纹只读识别，命中已知人标注 `[语音 说话人:名 置信:x]`，
  未命中标注未注册；绑定了实体时追加机器可解析的 `[speaker_scope:...]` 标签驱动记忆召回，见
  [声纹分册](audio-voiceprint.md)；realtime_speaker_annotate 可关）
- **通话状态注入**（`agent/realtime/context.py`，volatile 层）：通话期间注入挂载的通话频道信息与
  自动播报说明 + 「先应声再干活」训诫（工具/长任务前先 send_message 应一声再执行，工具轮静默即
  通话卡顿）；无通话零注入
- **通话入口**：聊天输入区通话条（`pages/chat/RealtimeCallBar.tsx`：开关/状态灯/实时转写/输入电平/
  输出音量）；声音页只保留能力管理面
