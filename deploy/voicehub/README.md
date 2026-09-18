# VoiceHub · 声音服务中枢

win-pc（192.168.1.4）声音侧全部服务的容器目录。职责：把外部音源（NAS OpenList / 频道语音）
经 **转写 + 说话人分离 + 声纹向量** 加工成结构化分段，供 AnelfAgent 音频库入库检索。

> 本服务**不持久化任何身份数据**——说话人档案 / 样本池 / 实体绑定 / 阈值门控全在 Agent 侧
> （`agent_audio.sqlite3`）。VoiceHub 只产「分段 + 文本 + 192 维向量」，记忆归 Agent。

技术栈：MOSS-Transcribe-Diarize 0.9B（主）· pyannote community-1（回退）· SenseVoice-Small ASR ·
ERes2NetV2 / CAM++ 声纹（sherpa-onnx ONNX）· FastAPI · NSSM 常驻 · uv 独立虚拟环境

---

## 0. 架构与决策链

```
外部音源（NAS OpenList /频道语音）
        │  audiosync 组件扫描
        ▼
┌─────────────────────────────────────────────────────────────┐
│ AnelfVoiceHub  :10096  编排器（兼容旧 FunASR /transcribe 契约）│
│                                                              │
│  ffmpeg → 16k mono wav                                       │
│     │                                                        │
│     ├─【主路径】POST :10100 MOSS ──→ 转写+分离一体            │
│     │            （带时间戳 + [S01]/[S02] 说话人编号）         │
│     │                                                        │
│     └─【回退】MOSS 失败/空结果时自动降级：                     │
│          POST :10098 pyannote 分离（exclusive turns）         │
│          POST :10099 SenseVoice ASR（逐 turn）                │
│     │                                                        │
│     ▼                                                        │
│  ERes2NetV2 声纹（sherpa-onnx ONNX，逐 turn 取 192 维向量）    │
│  低质量段 vector=None；分离失败回退单段                        │
└─────────────────────────────────────────────────────────────┘
        │  {segments:[{start_ms,end_ms,text,vector,speaker}]}
        ▼
AnelfAgent 音频库：声纹匹配（锚 + AS-Norm 分离度门）→ 归属/建档
```

**为什么 MOSS 当主路径**：单模型同时出转写与说话人分离，混录（两人以上同轨）场景质量明显优于
「先分离再逐段 ASR」的级联；实测 30s 混录 4.9s（rtf 0.164），输出自带时间戳与说话人编号。
级联链保留为自动回退，MOSS 挂了不断服。

---

## 1. 服务清单

| NSSM 服务 | 端口 | venv | 模型 | 显存常驻 | 角色 |
|---|---|---|---|---|---|
| `AnelfVoiceHub` | 10096 | `venvs\server` | —（编排 + ERes2NetV2 ONNX） | ~0（CPU 声纹） | 编排器，Agent 唯一入口 |
| `AnelfMoss` | 10100 | `venvs\moss` | MOSS-Transcribe-Diarize 0.9B（bf16） | **2048 MB** | 转写+分离主路径 |
| `AnelfDiarize` | 10098 | `venvs\diarize` | pyannote community-1（VBx+PLDA） | **360 MB** | 分离回退 |
| `AnelfAsr` | 10099 | `venvs\funasr` | SenseVoice-Small | **1264 MB** | ASR 回退 |

音频栈显存合计 **≈3.7 GB**（实测 2026-09-18 13:00，RTX 3090 24 GB）。
全部 `SERVICE_AUTO_START` + `AppExit=Restart`（延迟 5s）+ 日志 10MB 轮转。

> 人脸服务不在本目录，见同级 `..\face_server\`（:10097，另占 ≈990 MB）。

---

## 2. 目录结构

```
voicehub\
├─ server\            编排器与 worker 源码（server.py / moss_worker.py /
│                     diarize_worker.py / asr_worker.py / embedder.py）
├─ venvs\             4 个 uv 独立环境：server / moss / diarize / funasr（均 py3.12）
├─ models\            模型权重（各服务共用本目录，不跨服务外借）
│  ├─ moss\           MOSS-Transcribe-Diarize（safetensors + remote_code）
│  ├─ community-1\    pyannote 分离（segmentation/embedding/plda）
│  ├─ eres2netv2\     声纹主模型（modelscope 原始 ckpt）
│  ├─ campplus\       声纹回退模型
│  ├─ sensevoice\     SenseVoice-Small 权重
│  └─ onnx\           eres2netv2.onnx / campplus.onnx（sherpa-onnx 实际加载这两个）
├─ setup\             部署与修复脚本**权威源**（bootstrap.ps1 / prep_workers.ps1 /
│                     fix_*.ps1 / dl_all.py）——重建环境照这里跑
├─ tests\             验收脚本与样本（test_orch.py / test_moss_eval.py / mixrec_*.m4a …）
├─ logs\              运行日志（svc_*.log / svc_*.err.log / status.txt=部署进度总览）
├─ moss_repo\         MOSS 官方仓库（inference_utils / transcript_parser / prompts 参考）
└─ start_*.cmd        手工前台启动脚本（排障用，正常走 NSSM）
```

---

## 3. HTTP 契约

### 编排器 `:10096`（Agent 只对接这一个）

`GET /health`
```json
{"ok": true, "embedder": true, "dim": 192, "moss": true}
```
`moss:false` 表示 MOSS 服务不可达，编排器会自动走回退链（不影响可用性）。

`POST /transcribe`（`multipart/form-data`：`file` 音频 + 可选 `source_time`）
```json
{
  "file": "audio.m4a",
  "num_segments": 5,
  "engine": "moss",              // moss=主路径 / legacy=回退链
  "process_time_s": 5.91,
  "segments": [
    {"start_ms": 1280, "end_ms": 2220, "speaker": "S01",
     "text": "我讨厌你了。", "vector": [/* 192 floats */]}
  ]
}
```
- `vector` 为 `null` 表示该段太短/质量不足，Agent 侧不建档不匹配
- `source_time` 传毫秒时间戳或 ISO 串时，每段附 `abs_start_ms`/`abs_end_ms`（绝对时间轴）
- 说话人**只给簇编号**（S01/S02），身份判定在 Agent 侧

### MOSS `:10100`

`GET /health` → `{"ok":true,"model":"MOSS-Transcribe-Diarize-0.9B","device":"cuda","vram_gb":1.69}`
`POST /transcribe_diarize` `{"wav":"<16k mono wav 绝对路径>"}`
→ `{"segments":[{"start":1.28,"end":2.22,"speaker":"S01","text":"…"}],"duration_s":30.2,"process_time_s":4.9}`

### 回退链 `:10098` / `:10099`

`POST /diarize` `{"wav":"…"}` → `{"turns":[{"start":…,"end":…,"speaker":"S0"}]}`（exclusive 对齐）
`POST /asr` `{"wav":"…","turns":[…]}` → `{"results":[{"text":"…","emotion":…,"events":[…]}]}`
`:10098` 另有 `GET /memstat`（allocated/reserved/max_allocated，显存排查用）

---

## 4. 运维

```powershell
# 状态
Get-Service *Anelf*
nssm status AnelfVoiceHub          # nssm 在 D:\ServicesCenter\tools\nssm\

# 重启（改代码后）
D:\ServicesCenter\tools\nssm\nssm.exe restart AnelfVoiceHub

# 健康
curl http://127.0.0.1:10096/health
curl http://127.0.0.1:10100/health

# 端到端验收（期望 engine=moss、各段带 speaker 与 VEC）
D:\ServicesCenter\voicehub\venvs\server\Scripts\python.exe `
  D:\ServicesCenter\voicehub\tests\test_orch.py `
  D:\ServicesCenter\voicehub\tests\mixrec_combo.wav
```

日志：`logs\svc_<服务名>.log`（stdout）/ `.err.log`（stderr）。
**排查顺序**：`/health` → `svc_*.err.log` 尾部 → `nvidia-smi` 看显存是否被挤占。

---

## 5. 显存纪律（血泪教训，务必遵守）

1. **测性能前先看 `nvidia-smi` 空闲显存**。MOSS 曾测出「45s 音频推理 599s」，
   真因是 diarize 占了 21.8 GB 把它挤进共享内存分页；治理后同文件 **5.2s**，差 115 倍。
   显存不足时的耗时数据完全不可信，别据此做架构决策。
2. **常驻显存要主动管**：diarize worker 每请求后 `torch.cuda.empty_cache()` + batch 32→16，
   常驻从 21.8 GB 降到 360 MB。新增长驻模型时同样先估显存、后上线。
3. 与 ComfyUI 共存：音频栈 3.7 GB + 人脸 1.0 GB ≈ 4.7 GB 常驻，24 GB 卡剩 ~19 GB 给绘图/视频。
   跑大模型（Wan/H3 视频生成）**不要与重同步并行**，错峰。

---

## 6. 已知坑（重装/排障必读）

| 现象 | 真因 | 处置 |
|---|---|---|
| diarize 起不来，报 torchcodec FFmpeg DLL 版本错 | torchcodec 只认 FFmpeg 4–7，本机是 8/9 | `diarize_worker` 改用 soundfile 预解码成 waveform-dict 喂 pyannote（官方支持路径，彻底绕开） |
| 声纹 `provider='cuda'` 无效，日志 `Please compile with -DSHERPA_ONNX_ENABLE_GPU=ON` | sherpa-onnx Windows 轮子是 CPU-only | 接受 CPU：ERes2NetV2 dim192 是小模型，CPU 单段毫秒级，不是瓶颈 |
| MOSS 推理奇慢（百倍级） | 显存被其他服务挤占 → 落共享内存分页 | 见第 5 节；先 `nvidia-smi` 再下结论 |
| worker 里 ffmpeg 找不到 | NSSM 服务环境**不继承用户 PATH** | 在 worker 源码顶部显式前置 `D:\ServicesCenter\tools\ffmpeg\bin` 到 `os.environ["PATH"]` |
| `ModuleNotFoundError: fastapi` | 新建 venv 只装了模型依赖 | `uv pip install --python <venv>\Scripts\python.exe fastapi uvicorn pydantic python-multipart` |
| MOSS processor 调用报 `missing 1 required positional argument: 'audio'` | 它的 `__call__` 签名是 `(text, audio)`，不是 `processor(audio=…)` | 走 `apply_chat_template` → `processor(text=…, audio=[…])`，参考 `tests\test_moss_eval.py` |

---

## 7. Agent 侧对接

- 配置项 `funasr_endpoint = http://192.168.1.4:10096`（沿用旧键名，实际指向本编排器）
- `funasr_timeout = 600`（长音频 + MOSS 生成需要余量）
- 音源同步：`audiosync` 组件扫 `openlist:/个人数据/音源`，watch 60s 增量；
  重建用 `audiosync_rebuild(paths=<录制单元路径>)`
- 声纹阈值/建档/绑定策略全在 Agent 配置（`audio_match_threshold` 0.75、
  `audio_sample_coherence_floor` 0.45、`audio_min_segment_ms` 2000 等），本服务不参与身份判定

---

## 8. 环境重建

权威脚本在 `setup\`（bootstrap.ps1 装 venv 与依赖、dl_all.py 下模型、prep_workers.ps1 补 web 依赖）。
模型来源：MOSS 与 community-1 走 hf-mirror（非 gated），ERes2NetV2 / CAM++ / SenseVoice 走 modelscope。
重建后按第 4 节验收命令逐项过一遍再切流量。

---

_最后更新：2026-09-18（MOSS 升主路径 + 显存治理 + 五服务化）_
