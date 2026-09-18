"""FaceEngine 参考服务端 — InsightFace(buffalo_l) + ONNX Runtime GPU + FastAPI.

AnelfAgent 人脸子系统（agent/vision/face/engine.py）对接的外部识别服务。
职责单一：检测人脸 + 提取 512 维 ArcFace 向量。人脸库（人物档案/样本/
绑定/事件）全部在 Agent 侧，本服务不持久化任何身份数据。

HTTP 契约
=========
GET /health
    → 200 {"status":"ok","model":"buffalo_l","dim":512,"device":"cuda","version":"1.0"}

POST /extract   (multipart/form-data)
    字段:
      file          图片文件（jpeg/png/webp/bmp）
      min_det_score 可选，浮点，服务端按检测置信度预过滤（默认 0.0 不过滤）
      max_faces     可选，整数，最多返回的人脸数（按 det_score 降序，默认 0=不限）
    → 200 {"width":W,"height":H,"faces":[
              {"bbox":[x,y,w,h],"det_score":0.92,
               "pose":{"pitch":..,"yaw":..,"roll":..},
               "vector":[512 floats, L2 归一化]}]}
       faces 按 det_score 降序；vector 已 L2 归一化（余弦相似度=点积）。

错误契约（非 2xx 一律此结构）
    {"error":{"code":"<CODE>","message":"<可读说明>"}}
    INVALID_IMAGE    400  图片解码失败/为空
    IMAGE_TOO_LARGE  413  超过 MAX_UPLOAD_MB（默认 20MB）
    MODEL_NOT_READY  503  模型仍在加载
    ENGINE_ERROR     500  推理内部错误

环境变量
    FACE_HOST        监听地址（默认 0.0.0.0）
    FACE_PORT        监听端口（默认 10097）
    FACE_MODEL       InsightFace 模型名（默认 buffalo_l）
    FACE_DET_SIZE    检测输入边长（默认 640）
    FACE_CTX_ID      GPU 设备号（默认 0；无 GPU 自动回退 CPU）
    FACE_MAX_UPLOAD_MB  上传上限 MB（默认 20）
    FACE_DIM         向量维度声明（默认 512；首次推理后以实测为准）

运行
    uvicorn server:app --host 0.0.0.0 --port 10097
    （或 python server.py 走内置 uvicorn 启动）
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List

import numpy as np
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------

MODEL_NAME = os.getenv("FACE_MODEL", "buffalo_l")
DET_SIZE = int(os.getenv("FACE_DET_SIZE", "640"))
CTX_ID = int(os.getenv("FACE_CTX_ID", "0"))
MAX_UPLOAD_BYTES = int(os.getenv("FACE_MAX_UPLOAD_MB", "20")) * 1024 * 1024
DECLARED_DIM = int(os.getenv("FACE_DIM", "512"))
VERSION = "1.0"


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message}})


# ------------------------------------------------------------------
# 模型生命周期
# ------------------------------------------------------------------

class Engine:
    """InsightFace FaceAnalysis 封装（懒加载 + 就绪标志 + 实测维度）。"""

    def __init__(self) -> None:
        self.app: Any = None
        self.ready = False
        self.device = "cpu"
        self.dim = DECLARED_DIM

    def load(self) -> None:
        from insightface.app import FaceAnalysis
        # CUDA 优先，无 GPU 时 onnxruntime 自动回退 CPU
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.app = FaceAnalysis(name=MODEL_NAME, providers=providers)
        # ctx_id>=0 用 GPU；det_size 为检测输入分辨率
        self.app.prepare(ctx_id=CTX_ID, det_size=(DET_SIZE, DET_SIZE))
        try:
            used = self.app.models[0].session.get_providers() \
                if getattr(self.app, "models", None) else []
            self.device = "cuda" if any("CUDA" in p for p in used) else "cpu"
        except Exception:
            self.device = "cuda" if CTX_ID >= 0 else "cpu"
        self.ready = True

    def extract(self, img_bgr: np.ndarray, min_det_score: float,
                max_faces: int) -> List[Dict[str, Any]]:
        faces = self.app.get(img_bgr)
        out: List[Dict[str, Any]] = []
        for f in faces:
            score = float(getattr(f, "det_score", 0.0))
            if score < min_det_score:
                continue
            emb = np.asarray(f.normed_embedding if getattr(f, "normed_embedding", None)
                             is not None else f.embedding, dtype=np.float64).ravel()
            norm = float(np.linalg.norm(emb))
            if norm > 0:
                emb = emb / norm  # 契约要求 L2 归一化
            self.dim = int(emb.shape[0])
            x1, y1, x2, y2 = [float(v) for v in getattr(f, "bbox", [0, 0, 0, 0])]
            pose = getattr(f, "pose", None)
            pitch = yaw = roll = 0.0
            if pose is not None and len(pose) >= 3:
                pitch, yaw, roll = (float(pose[0]), float(pose[1]), float(pose[2]))
            out.append({
                "bbox": [x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)],
                "det_score": round(score, 4),
                "pose": {"pitch": round(pitch, 2), "yaw": round(yaw, 2),
                         "roll": round(roll, 2)},
                "vector": [round(float(v), 6) for v in emb.tolist()],
            })
        out.sort(key=lambda d: d["det_score"], reverse=True)
        if max_faces > 0:
            out = out[:max_faces]
        return out


engine = Engine()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动即加载模型（阻塞首请求前完成；失败保持 not ready，/extract 返回 503）
    try:
        engine.load()
    except Exception as exc:  # noqa: BLE001
        print(f"[face_server] 模型加载失败: {exc}", flush=True)
    yield


app = FastAPI(title="AnelfAgent FaceEngine", version=VERSION, lifespan=lifespan)


# ------------------------------------------------------------------
# 路由
# ------------------------------------------------------------------

@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok" if engine.ready else "loading",
        "model": MODEL_NAME,
        "dim": engine.dim,
        "device": engine.device,
        "version": VERSION,
    }


@app.post("/extract")
async def extract(
    request: Request,
    file: UploadFile = File(...),
    min_det_score: float = Form(default=0.0),
    max_faces: int = Form(default=0),
) -> Any:
    if not engine.ready:
        return _error("MODEL_NOT_READY", "识别模型仍在加载，请稍后重试", 503)

    data = await file.read()
    if not data:
        return _error("INVALID_IMAGE", "上传内容为空", 400)
    if len(data) > MAX_UPLOAD_BYTES:
        return _error("IMAGE_TOO_LARGE",
                      f"图片超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限", 413)

    try:
        import cv2
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return _error("INVALID_IMAGE", "图片解码失败（损坏或不支持的格式）", 400)
        height, width = img.shape[:2]
        faces = engine.extract(img, max(0.0, min_det_score), max(0, max_faces))
        return {"width": int(width), "height": int(height), "faces": faces}
    except Exception as exc:  # noqa: BLE001
        return _error("ENGINE_ERROR", f"推理内部错误: {exc}", 500)


def main() -> None:
    import uvicorn
    host = os.getenv("FACE_HOST", "0.0.0.0")
    port = int(os.getenv("FACE_PORT", "10097"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
