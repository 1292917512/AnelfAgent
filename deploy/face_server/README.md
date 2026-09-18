# FaceEngine 人脸识别服务 — Windows(3090) 部署方案

AnelfAgent 人脸子系统的**外部识别引擎**。职责单一：检测人脸 + 提取 512 维
ArcFace 向量。人脸库（人物档案 / 样本池 / 实体绑定 / 出现事件）全部在
Agent 侧，本服务不持久化任何身份数据——它只是"眼睛的视网膜"，记忆归 Agent。

> 技术栈：InsightFace（buffalo_l = SCRFD 检测 + ArcFace-512 识别）· ONNX Runtime GPU · FastAPI

---

## 0. 架构定位

```
Windows 3090 机器（本服务）              AnelfAgent 主机
┌──────────────────────────┐           ┌────────────────────────────────┐
│ server.py (FastAPI)       │  HTTP     │ agent/vision/face/engine.py     │
│  GET  /health             │◀─────────│  探活 + 可观测性                 │
│  POST /extract            │           │  extract_faces()                │
│  InsightFace + ONNX GPU   │           │        │                         │
│  只产向量，不存身份         │           │        ▼                         │
└──────────────────────────┘           │  store.py(SQLite 卷 "face")     │
                                        │  matcher.py(锚+AS-Norm 分离度门) │
                                        │  → 人物档案/绑定/事件/自动召回     │
                                        └────────────────────────────────┘
```

---

## 1. 环境准备（Windows + RTX 3090）

### 1.1 前置

- **Python 3.10 或 3.11**（64 位）。InsightFace 在 3.10/3.11 上轮子最全；3.12 可能需要源码编译，不推荐。
  下载：<https://www.python.org/downloads/windows/>（安装时勾选 *Add python.exe to PATH*）
- **NVIDIA 驱动**：支持 RTX 3090 的较新 Game Ready / Studio 驱动（`nvidia-smi` 能看到显卡即可）。
- **CUDA + cuDNN**（GPU 推理需要）：RTX 3090 是 Ampere 架构。
  - 路线 A（推荐，省事）：装 **CUDA 11.8 + cuDNN 8.x**，配 `onnxruntime-gpu==1.16.*`/`1.17.*`
  - 路线 B：装 **CUDA 12.x + cuDNN 9.x**，配 `onnxruntime-gpu>=1.18`
  - onnxruntime-gpu 与 CUDA/cuDNN 的版本对应表见
    <https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#requirements>
- **Visual C++ 生成工具**（仅当某些包需要源码编译时）：
  <https://visualstudio.microsoft.com/visual-cpp-build-tools/>

> 想先不碰 GPU？把 `requirements.txt` 里的 `onnxruntime-gpu` 换成 `onnxruntime`，
> 服务会自动跑 CPU（3090 上 GPU 单图约 10–30ms，CPU 约 200–500ms，契约完全一致）。

### 1.2 创建虚拟环境

在 PowerShell 中：

```powershell
cd C:\path\to\face_server          # 把本目录拷到 Windows 机器
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

> 若 `Activate.ps1` 被脚本策略拦截，先执行：
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

### 1.3 安装依赖（顺序关键）

**先**装匹配 CUDA 的 onnxruntime-gpu，**再**装其余依赖——否则 insightface 可能
把 CPU 版 onnxruntime 拉进来覆盖 GPU 版。

```powershell
# 1) 先装 GPU 运行时（按你的 CUDA 选版本；下面是 CUDA 11.8 的例子）
pip install onnxruntime-gpu==1.17.1

# 2) 再装其余依赖（requirements 里已排除重复的 onnxruntime-gpu 冲突，
#    若 pip 试图降级/替换 onnxruntime-gpu，加 --no-deps 单独补其余包）
pip install fastapi "uvicorn[standard]" python-multipart numpy opencv-python insightface
```

验证 GPU 后端可用：

```powershell
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
# 期望输出包含 'CUDAExecutionProvider'
```

若只看到 `CPUExecutionProvider`，说明 CUDA/cuDNN 版本与 onnxruntime-gpu 不匹配，
回到 1.1 核对版本对应表。

---

## 2. 模型下载

InsightFace 首次运行会自动下载 `buffalo_l` 模型包到用户目录：

```
C:\Users\<你>\.insightface\models\buffalo_l\
```

- 服务器可联网：直接启动即可（见第 3 节），首次启动会自动下载（约 280MB）。
- 服务器**离线**：在能联网的机器上下载后，把整个 `buffalo_l` 目录拷到上面的路径。
  模型包地址：<https://github.com/deepinsight/insightface/releases>（找 `buffalo_l.zip`），
  解压后目录结构应为 `buffalo_l/{det_10g.onnx, w600k_r50.onnx, ...}`。

---

## 3. 启动服务

### 3.1 前台试跑（先验证）

```powershell
# 默认监听 0.0.0.0:10097，GPU 设备 0
python server.py
```

看到 `Uvicorn running on http://0.0.0.0:10097` 且无模型加载报错即成功。
另开一个 PowerShell 验收（见第 5 节）。

### 3.2 常用环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `FACE_HOST` | `0.0.0.0` | 监听地址 |
| `FACE_PORT` | `10097` | 监听端口 |
| `FACE_MODEL` | `buffalo_l` | InsightFace 模型名 |
| `FACE_DET_SIZE` | `640` | 检测输入边长（小脸多可调 960 提精度，更慢） |
| `FACE_CTX_ID` | `0` | GPU 设备号 |
| `FACE_MAX_UPLOAD_MB` | `20` | 上传上限 |

示例（改端口 + 大检测分辨率）：

```powershell
$env:FACE_PORT="10097"; $env:FACE_DET_SIZE="960"; python server.py
```

### 3.3 常驻运行（开机自启、崩溃自拉起）

推荐 **NSSM**（把脚本注册成 Windows 服务，最稳）：

1. 下载 NSSM：<https://nssm.cc/download>，解压取 `win64\nssm.exe`。
2. 注册服务（PowerShell 管理员）：

```powershell
nssm install FaceEngine "C:\path\to\face_server\.venv\Scripts\python.exe" "C:\path\to\face_server\server.py"
nssm set FaceEngine AppDirectory "C:\path\to\face_server"
nssm set FaceEngine AppEnvironmentExtra FACE_PORT=10097 FACE_CTX_ID=0
nssm set FaceEngine AppStdout "C:\path\to\face_server\face_engine.log"
nssm set FaceEngine AppStderr "C:\path\to\face_server\face_engine.err.log"
nssm start FaceEngine
```

管理：`nssm restart FaceEngine` / `nssm stop FaceEngine` / `nssm remove FaceEngine confirm`。

> 备选：**任务计划程序**（Task Scheduler）建"计算机启动时"触发的任务，
> 操作指向 `.venv\Scripts\python.exe`，参数 `server.py`，勾选"不管用户是否登录都运行"。
> 但 NSSM 的崩溃自拉起更省心，优先 NSSM。

### 3.4 防火墙放行

让 AnelfAgent 主机能访问本机 10097 端口（管理员 PowerShell）：

```powershell
New-NetFirewallRule -DisplayName "FaceEngine 10097" -Direction Inbound -LocalPort 10097 -Protocol TCP -Action Allow
```

> 安全建议：该服务无鉴权，**只应在可信内网暴露**。若跨网段，用防火墙限定
> 源 IP 为 AnelfAgent 主机，或套一层反向代理加 Token。

---

## 4. Agent 侧接入（回到 AnelfAgent 主机）

1. 打开 AnelfAgent Web 面板 → **模型页 → 组件凭据**，找到 **人脸识别服务（face）** 卡片，
   在「服务地址」填 Windows 机器的地址，例如：

   ```
   http://192.168.1.50:10097
   ```

   （或直接编辑 `config/provider_keys.json` 的 `face.face_endpoint` 字段。）

2. 打开 **视觉页 → 人脸识别 Tab → 总览**，点「重新检测」：
   - 徽标变绿「可达」、显示模型 `buffalo_l` / 维度 `512` / 设备 `cuda` 即接通。

3. 按需开启自动识别（**视觉页 → 配置 → vision/face 组**，或配置中心）：
   - `face_auto_ingest`（默认开）：频道入站图片自动识别，命中已绑定实体时打
     `face_scope` 标驱动该实体的画像/记忆自动召回。
   - `face_watch_enabled`（默认关，隐私敏感）：视觉源监视帧自动识别。
   - `face_match_threshold`（默认 0.40）：**部署后务必用你的库标定**——
     注册几个真人 + 几个陌生人，观察相似度分布，把判定线设在"同人最低分"与
     "异人最高分"之间。ArcFace 典型区间 0.3–0.5。

4. 注册第一个人物：视觉页 → 人脸识别 → 人物档案 → 「注册人脸」，
   上传正脸照、填姓名、可选填实体 scope（如 `user:qq:456`）。
   之后该人出现在频道图片中即被认出并召回其记忆。

---

## 5. 验收（curl / PowerShell）

在 Windows 本机或同网段机器：

```powershell
# 健康检查
curl http://127.0.0.1:10097/health
# 期望: {"status":"ok","model":"buffalo_l","dim":512,"device":"cuda","version":"1.0"}

# 人脸提取（替换 test.jpg 为一张含人脸的图片）
curl -X POST http://127.0.0.1:10097/extract -F "file=@test.jpg" -F "min_det_score=0.5"
# 期望: {"width":..,"height":..,"faces":[{"bbox":[x,y,w,h],"det_score":..,"pose":{..},"vector":[512 个数]}]}

# 错误契约：传一张非图片
curl -X POST http://127.0.0.1:10097/extract -F "file=@requirements.txt"
# 期望: {"error":{"code":"INVALID_IMAGE","message":"..."}}（HTTP 400）
```

PowerShell 原生（无 curl 时）：

```powershell
Invoke-RestMethod http://127.0.0.1:10097/health
```

---

## 6. HTTP 契约（权威定义）

### GET /health

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | str | `ok`（就绪）/ `loading`（模型加载中） |
| `model` | str | 模型名（`buffalo_l`） |
| `dim` | int | 向量维度（512；首次推理后以实测为准） |
| `device` | str | 推理设备（`cuda` / `cpu`） |
| `version` | str | 服务版本 |

### POST /extract

请求 `multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | file | 是 | 图片（jpeg/png/webp/bmp） |
| `min_det_score` | float | 否 | 服务端按检测置信度预过滤（默认 0.0） |
| `max_faces` | int | 否 | 最多返回人脸数，按 det_score 降序（默认 0=不限） |

响应 200：

```json
{
  "width": 1280, "height": 720,
  "faces": [
    {
      "bbox": [x, y, w, h],
      "det_score": 0.92,
      "pose": {"pitch": 3.1, "yaw": -7.2, "roll": 1.0},
      "vector": [/* 512 floats, L2 归一化 */]
    }
  ]
}
```

- `bbox` 为 `[x, y, w, h]`（左上角 + 宽高，像素）。
- `vector` **已 L2 归一化**，余弦相似度 = 点积。
- `faces` 按 `det_score` 降序。
- `pose` 为姿态角（度，正脸≈0），Agent 侧据此算质量权重（正脸高清样本锚折叠贡献更高）。

### 错误契约（非 2xx 一律此结构）

```json
{"error": {"code": "INVALID_IMAGE", "message": "图片解码失败"}}
```

| code | HTTP | 含义 | Agent 侧处理 |
|------|------|------|-------------|
| `INVALID_IMAGE` | 400 | 解码失败/为空 | 不重试 |
| `IMAGE_TOO_LARGE` | 413 | 超上传上限 | 不重试 |
| `MODEL_NOT_READY` | 503 | 模型加载中 | 可重试 |
| `ENGINE_ERROR` | 500 | 推理内部错误 | 可重试 |
| （网络/超时） | — | 连不上 | Agent 侧自动重试一次后 fail-open |

---

## 7. 常见问题

- **`CUDAExecutionProvider` 不可用 / 回退 CPU**：onnxruntime-gpu 与 CUDA/cuDNN 版本不匹配。
  核对第 1.1 节版本对应表，重装匹配的 onnxruntime-gpu。
- **首次启动卡在下载模型**：正常（下 buffalo_l ~280MB）；离线环境按第 2 节手动放置模型。
- **`ModuleNotFoundError: No module named 'cv2'`**：漏装 `opencv-python`。
- **Agent 侧显示"不可达"但本机 curl 正常**：防火墙未放行（3.4）或填的是 `127.0.0.1`
  （Agent 在另一台机器，必须填 Windows 机器的局域网 IP）。
- **识别总是认错/认不出**：`face_match_threshold` 未按实际库标定（第 4 节第 3 步）；
  或注册样本是侧脸/糊图——用正脸高清照重新注册，并让人物多累积几张不同角度的样本。
