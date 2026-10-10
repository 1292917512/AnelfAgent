# 音频与声纹

音频核心库、声纹身份体系、音色预设与语音组件的现状说明。修改 `agent/audio/`、`agent/tts/` 或
audiosync/dashscope 实体时按需阅读；实时通话链路见[实时语音分册](realtime-voice.md)。

## 音频核心层

- **提供者注册表**（`agent/audio/providers.py`）：ASR 转写 / 声纹提取的接口抽象（Protocol +
  kind/priority/check_available），实现以组件形式注册（entities 经 `_sdk.register_audio_provider`
  桥接）；`registry.resolve(kind)` 按优先级链取首个可用者。FunASR 组件在
  `entities/audiosync/funasr_provider.py`
- **音频核心库**（`agent/audio/store.py`，存储卷 "audio"，默认主库同族 `{stem}_audio.sqlite3`）：
  audio_segments（转写/归属/未读/录制归属）+ speakers（声纹身份 + entity_scope 绑定列）+
  voice_samples + recordings 四表一体；FTS5 走预分词列（与记忆库同一 tokenizer），sqlite-vec
  派生索引缺失时 Python 余弦降级
- **匹配/入库/整理/复听**：`matcher.py` / `ingest.py` / `consolidate.py` / `listen.py` 全部核心化
  （配置键 audio_*）；回听的源文件取回走 `source_fetch.py` 取回器链（本地直读兜底；远程下载由
  audiosync 实体注册）
- **音频核心服务**（`service.py`）：transcribe / speaker_embed 经提供者链；`ingest_payload` /
  `transcribe_and_store` 走核心入库管线（噪音过滤 → 声纹识别 → 落库 → 向量回填 wake）
- **音源同步实体**（`entities/audiosync/`）：只做「音源 → 核心库」搬运——目录镜像同步（来源组件化：
  `framework.AudioSyncSource` 契约 + sources/ 扫描注册，local_dir/OpenList 两组件）+ 上游推送
  （ingest 端点，token 自校验 fail-closed）+ FunASR 组件注册（funasr_endpoint/funasr_timeout，
  真实可达性探测 + 短 TTL 缓存）+ 出站 webhook。配置键 audiosync_*（entity/audiosync 组）
- **能力域工具**（audio 组）：voice_to_text（`ingest=true` 一步转写+入音源库，受 audio_ai_enabled
  门控）/ text_to_voice / generate_music / generate_lyrics 常驻 always + 音色管理与 voice_preset/
  sound_config 配置工具 core；voice_to_text 统一进核心 ASR 链（内部 ModelsAsrProvider 桥接 asr
  模型链）。能力路由框架见[能力路由分册](vision-and-capabilities.md)

## 声纹体系（一人一档案 + 信道感知）

工业界主流方案（多段注册 → 聚合鲁棒表示 → 信道补偿 → 低学习率动态更新）：

| 机制 | 位置 | 说明 |
|------|------|------|
| 声纹锚 | `speakers.vector + anchor_weight` + `vectors.py::blend/weighted_centroid` | 锚 = 全部历史合格样本的**时长加权质心**（权重=时长秒截断 [0.5,10]），每次合格采样增量折叠——学习率 1/(n+1) 自然衰减；加权质心满足结合律 → 合并可精确合成（按累计权重 blend，与重放全部历史样本等价） |
| 信道感知 | `voice_samples.channel/duration_ms` + `channels.py::normalize_channel` | 同一人经微信/电话/麦克风提取的嵌入有信道漂移：样本带信道标注（voip/mic/web/enroll/chat/phone），匹配评分 = max(锚, **同信道加权质心模板**, 最佳样本)；池满淘汰同信道最早样本（信道涌入只挤占自己，多样性自保持） |
| 防投毒门控 | `store.add_sample` 相干门 + `audio_sample_coherence_floor`（默认 0.45） | 与锚余弦低于门限的样本拒入（错认人/噪音），远低于匹配阈值不拦信道漂移；同名 enroll 累积也过门（返回 sample_rejected，防张冠李戴） |
| 匹配架构 | `matcher.match_vector` | 锚全量扫描入围（阈值-0.25 或 TopN）→ 候选三判据精评（anchor/sample/channel 分值，可解释「为什么认成他」）；候选四元组含 separation（置信分离度） |
| numpy 向量代数 | `vectors.py`（cosine/cosine_many/unit_rows/pairwise_sims，numpy 2.x） | 全库锚扫描 = 归一矩阵 × 查询向量一次矩阵积；对外签名/返回类型不变（np 标量在 matcher 出口统一转换） |
| AS-Norm 分离度门 | `matcher._cohort_separation` + `audio_match_separation`（默认 2.0，0=关） | 业界标准打分后端：候选锚得分对其余说话人 top-K 冒充分布做 z 归一——「好几个人都像」的模糊查询即使过了余弦阈值也降级为临时说话人待确认（防投毒优先于防分裂），cohort <3 人时门自动不启用；separation 作为独立判据字段返回（可解释「为什么不敢认」） |
| 阈值标定 | `agent/audio/__init__.py` + `matcher.py` 回退默认 | 匹配阈值 0.65（分离带中段：真人 p25 0.77 / 不同人 ≤0.55；12 档案 75 样本 192 维 cam++ 实测），合并阈值 0.60（比匹配宽松才能收拢同人分裂档案）；误认由分离度门（z≥2.0）第二判据兜底 |
| enroll 防分裂 | `matcher.enroll` | 一人一档案：重复注册同名不再裂出新档案；consolidate 聚类以锚为中心；`refine` 语义为**以当前样本池重建锚**（剔除坏样本后复位） |
| schema 版本门 | `store._sync_schema`（`PRAGMA user_version`） | 版本落后先 DROP 声纹表再按当前布局重建（声纹可再生，转写/录制登记/FTS 保留）。**纪律：声纹表结构变更必须 bump 版本号** |

**设计权衡明示**：相干门（0.45）的「宽」是新信道适应的前提——新信道样本对锚 cos≈0.61，任何能拦
0.78 攻击样本的门都会先拦死它；适应性投毒要求攻击者持有受害者足量语音，本地单用户场景在威胁模型
外，恢复通道（删样本+重建）AI/Web 双面可用。

## 声纹即记忆：实体关联与召回

- **实体关联**：`speakers.entity_scope` 绑定（speaker_bind AI/Web 双面，user:/group:/agent: 前缀
  校验）双向可查；片段查询结果 JOIN 携带 entity_scope——AI 检索话语即知「这是哪个实体说的」；
  每轮注入「声纹关联实体」清单（有声纹绑定时）
- **speaker_scope 标签召回**（`core/tags.py` + `recollection._speaker_scopes`）：通话中声纹命中且
  说话人绑定了实体时，标注追加机器可解析的 `[speaker_scope:user:qq:456]`；召回层解析并归一为
  权威 entity_scope 格式（绑定格式 ↔ 思维层 scope 方言的单点转换）——**私聊/通话同样生效**，
  听到谁说话就自动召回谁的画像/关系网络/相关记忆
- **精确对比工具**：`speaker_compare`（说话人 vs 说话人：锚对锚 + 样本最佳配对 + 同信道模板交叉 +
  相对合并阈值的判读——合并/绑定前的终极核查）；`voice_compare`（两段音频是否同一人）
- **实体维度检索**：`store.list_segments/search_segments(entity_scope=)` + `transcript_search(entity=)`
  ——「这个实体说过什么」直达
- **AI 闭环**：识别（voice_identify）→ 关联（speaker_bind）→ 重建（speaker_refine）→ 合并
  （speaker_merge）
- **格式纪律**：音频库绑定用 `user:qq:123`（记忆图谱节点 key 同族），思维层 scope 用
  `user_qq:123`，转换只发生在 `_speaker_scopes` 单点

## 音色预设制

- **预设注册表**（`agent/tts/presets.py` + `config/voice_presets.json`，gitignored 用户状态）：
  预设 = 命名的音色单元（预置音色 ID 或克隆参考对二选一 + 注释），线程锁+原子写；预设名全库查重；
  被场景指派的预设拒绝删除（先解除指派）；指派悬空按未指派处理不炸
- **场景指派**：配置键 `sound_voice_default`（全局默认）/ `sound_voice_realtime`（通话专用，空=
  跟随默认；克隆型预设不适用流式通话，落协议音色）
- **解析链**（`agent/tts/voice.py`）：场景指派 → 预设内容 → 提供者协议音色——全部合成入口
  （text_to_voice/级联通话/native 会话/主动播报/内置提供者）经此单一决策链；链序兜底：调用点
  显式音色 → 场景覆盖 → 全局默认 → 提供者协议音色。例外：小度音箱保留设备协议音色
- **AI/Web 同库同权**：`voice_preset` 工具（list/save/delete/apply）+ Web 声音页「音色」页签
  （/audio/voice-presets CRUD+assign）；实体组件经 `_sdk.default_tts_voice / realtime_tts_voice`
  取统一音色；clone_voice/design_voice 后 save+apply 即全链路换声

## 语音组件

- **阿里百炼**（`entities/dashscope/`）：流式 ASR（Recognition 流式会话，SDK 回调线程经
  call_soon_threadsafe 桥入事件循环）→ asr_stream 链；非流式转写 → asr 链；流式 TTS
  （SpeechSynthesizer 双向流式 PCM 直出）→ 核心 TTS 注册表；声音能力（一次性合成 + 音色管理：
  VoiceEnrollmentService 复刻/列表/删除，复刻需公网音频 URL）。Key 解析顺序：dashscope_api_key →
  llm_clients 中 dashscope 兼容提供者 → DASHSCOPE_API_KEY 环境变量；dashscope SDK 为可选依赖
  （未装/无 Key 组件整体不可用，链自动降级）
- **MiniMax**（`entities/minimax/`）：视觉/声音/检索/流式 TTS 四类组件注册进核心路由，配置自管于
  实体 config.json，删除目录即整体拔出

## Web 能力页

声音页（/sound）：总览（提供者链/库统计/注入情况）/实时对话/生成（能力链 + 音色预设）/说话人/
时间线/话语检索/识别入库/语音会话页签；数据面 `/api/audio/*`（services/audio.py 收口）。
