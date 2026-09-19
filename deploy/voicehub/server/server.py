# -*- coding: utf-8 -*-
"""VoiceHub 编排器（端口 10096）——兼容旧 FunASR /transcribe 契约。
决策链（09-18 升级）：ffmpeg → MOSS 转写+分离（主路径, :10100）→ ERes2NetV2/CAM++ 声纹(ONNX)。
MOSS 失败自动回退旧链：pyannote 分离(:10098) → SenseVoice ASR(:10099)。
低质量段 vector=None；分离失败回退单段。声纹匹配(is_miaomiao/unknown 门控)在 Agent 侧（持有说话人库）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

os.environ["PATH"] = r"D:\ServicesCenter\tools\ffmpeg\bin;" + r"D:\ServicesCenter\tools\ffmpeg-shared\bin;" + os.environ.get("PATH", "")
import httpx
import soundfile as sf
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from embedder import Embedder

FFMPEG = r"D:\ServicesCenter\tools\ffmpeg\bin\ffmpeg.exe"
MOSS_URL = "http://127.0.0.1:10100/transcribe_diarize"
MOSS_HEALTH = "http://127.0.0.1:10100/health"
DIARIZE_URL = "http://127.0.0.1:10098/diarize"
ASR_URL = "http://127.0.0.1:10099/asr"
ERES_ONNX = r"D:\ServicesCenter\voicehub\models\onnx\eres2netv2.onnx"
CAMP_ONNX = r"D:\ServicesCenter\voicehub\models\onnx\campplus.onnx"

app = FastAPI()
_embedder = None
_emb_lock = threading.Lock()
_infer_lock = threading.Lock()


def get_embedder():
    global _embedder
    if _embedder is None:
        with _emb_lock:
            if _embedder is None:
                for path in (ERES_ONNX, CAMP_ONNX):
                    if os.path.exists(path):
                        try:
                            _embedder = Embedder(path)
                            print("[orch] embedder loaded %s dim=%d" % (path, _embedder.dim), flush=True)
                            break
                        except Exception as e:
                            print("[orch] embedder fail %s: %r" % (path, e), flush=True)
                if _embedder is None:
                    print("[orch] NO embedder available", flush=True)
    return _embedder


def to_wav16k(src, dst):
    r = subprocess.run([FFMPEG, "-y", "-i", src, "-ar", "16000", "-ac", "1", "-f", "wav", dst],
                       capture_output=True)
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0


def parse_source_time(s):
    if not s:
        return None
    s = str(s).strip()
    if s.isdigit():
        return int(s)
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def moss_transcribe(wav):
    """主路径：MOSS 单模型转写+说话人分离。返回 (turns, asr_results)；失败抛异常由调用方回退。"""
    with httpx.Client(timeout=1800, trust_env=False) as c:
        r = c.post(MOSS_URL, json={"wav": wav})
        if r.status_code != 200:
            raise RuntimeError("moss http %d: %s" % (r.status_code, r.text[:200]))
        segs = r.json().get("segments", [])
    if not segs:
        raise RuntimeError("moss returned empty segments")
    turns = [{"start": float(s["start"]), "end": float(s["end"]),
              "speaker": s.get("speaker", "S0")} for s in segs]
    asr_results = [{"text": s.get("text", "") or ""} for s in segs]
    return turns, asr_results


def legacy_transcribe(wav, dur):
    """回退旧链：pyannote 分离 + SenseVoice ASR。返回 (turns, asr_results)。"""
    turns = []
    try:
        with httpx.Client(timeout=600, trust_env=False) as c:
            r = c.post(DIARIZE_URL, json={"wav": wav})
            if r.status_code == 200:
                turns = r.json().get("turns", [])
    except Exception as e:
        print("[orch] diarize fail: %r" % e, flush=True)
    if not turns:
        turns = [{"start": 0.0, "end": dur, "speaker": "S0"}]
    asr_results = []
    try:
        with httpx.Client(timeout=600, trust_env=False) as c:
            r = c.post(ASR_URL, json={"wav": wav, "turns": turns})
            if r.status_code == 200:
                asr_results = r.json().get("results", [])
    except Exception as e:
        print("[orch] asr fail: %r" % e, flush=True)
    return turns, asr_results


@app.get("/health")
def health():
    emb = get_embedder()
    moss_ok = False
    try:
        with httpx.Client(timeout=5, trust_env=False) as c:
            moss_ok = bool(c.get(MOSS_HEALTH).json().get("ok"))
    except Exception:
        pass
    return {"ok": True, "embedder": emb is not None,
            "dim": emb.dim if emb else 0, "moss": moss_ok}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...), source_time: str = Form("")):
    t0 = time.time()
    tmpdir = tempfile.mkdtemp(prefix="vh_")
    ext = os.path.splitext(file.filename or "")[1] or ".wav"
    src = os.path.join(tmpdir, "input" + ext)
    wav = os.path.join(tmpdir, "audio.wav")
    try:
        data = await file.read()
        with open(src, "wb") as f:
            f.write(data)
        if not to_wav16k(src, wav):
            return JSONResponse({"error": "ffmpeg convert failed"}, status_code=400)
        audio, sr = sf.read(wav, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        dur = len(audio) / sr if sr else 0.0
        with _infer_lock:
            # 1) 转写+分离：MOSS 主路径，失败回退旧链
            engine = "moss"
            try:
                turns, asr_results = moss_transcribe(wav)
            except Exception as e:
                print("[orch] moss fail, fallback to legacy: %r" % e, flush=True)
                engine = "legacy"
                turns, asr_results = legacy_transcribe(wav, dur)
            # 2) 声纹（ONNX 独立模块，逐 turn）
            emb = get_embedder()
            src_ms = parse_source_time(source_time)
            segments = []
            for i, t in enumerate(turns):
                start_ms = int(float(t["start"]) * 1000)
                end_ms = int(float(t["end"]) * 1000)
                text, emotion, events = "", None, []
                if i < len(asr_results):
                    ar = asr_results[i]
                    text = ar.get("text", "") or ""
                    emotion = ar.get("emotion")
                    events = ar.get("events") or []
                vec = None
                if emb is not None:
                    try:
                        v = emb.embed(wav, float(t["start"]), float(t["end"]))
                        if v is not None:
                            vec = [float(x) for x in v.tolist()]
                    except Exception as e:
                        print("[orch] embed fail: %r" % e, flush=True)
                seg = {"start_ms": start_ms, "end_ms": end_ms, "text": text,
                       "vector": vec, "speaker": t.get("speaker")}
                if emotion:
                    seg["emotion"] = emotion
                if events:
                    seg["events"] = events
                if src_ms is not None:
                    seg["abs_start_ms"] = src_ms + start_ms
                    seg["abs_end_ms"] = src_ms + end_ms
                segments.append(seg)
        if not segments:
            segments = [{"start_ms": 0, "end_ms": int(dur * 1000), "text": "", "vector": None}]
        return JSONResponse({"file": os.path.basename(file.filename or "input"),
                             "num_segments": len(segments),
                             "engine": engine,
                             "process_time_s": round(time.time() - t0, 2),
                             "segments": segments})
    except Exception as e:
        return JSONResponse({"error": str(e)[-2000:]}, status_code=500)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10096)
