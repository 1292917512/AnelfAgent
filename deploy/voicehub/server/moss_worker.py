# -*- coding: utf-8 -*-
"""MOSS-Transcribe-Diarize worker (port 10100)
混录专项：单模型完成转写+说话人分离，替代 pyannote+SenseVoice 双服务。
契约: POST /transcribe_diarize {"wav": "<16k mono wav path>"}
  -> {"segments": [{"start": float, "end": float, "speaker": "S01", "text": "..."}], "process_time_s": float}
"""
import os
import re
import time
import traceback

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
# NSSM 服务环境无用户 PATH：显式挂 ffmpeg（load_audio 解码依赖）
os.environ["PATH"] = r"D:\ServicesCenter\tools\ffmpeg\bin;" + r"D:\ServicesCenter\tools\ffmpeg-shared\bin;" + os.environ.get("PATH", "")

import torch
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

MODEL_PATH = r"D:\ServicesCenter\voicehub\models\moss"
PROMPT = (
    "请将音频转写为文本，每一段需以起始时间戳和说话人编号（[S01]、[S02]、[S03]…）开头，"
    "正文为对应的语音内容，并在段末标注结束时间戳，以清晰标明该段语音范围。"
)

app = FastAPI(title="MOSS Diarize Worker")
_model = None
_proc = None


def get_model():
    global _model, _proc
    if _model is None:
        from transformers import AutoModelForCausalLM, AutoProcessor
        t0 = time.time()
        _proc = AutoProcessor.from_pretrained(MODEL_PATH, trust_remote_code=True)
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_PATH, trust_remote_code=True,
            dtype=torch.bfloat16, device_map="cuda")
        print("[moss] loaded %.1fs VRAM %.2fGB" % (
            time.time() - t0, torch.cuda.memory_allocated() / 1024**3), flush=True)
    return _model, _proc


def parse_moss_output(text: str):
    """解析 MOSS 输出: [start][Sxx]content[end][start][Sxx]content[end]..."""
    text = re.sub(r'<\|[^|]*\|>', '', text)
    segments = []
    pattern = r'\[(\d+\.?\d*)\]\[(S\d+)\](.*?)\[(\d+\.?\d*)\]'
    for m in re.finditer(pattern, text, re.DOTALL):
        start, speaker, content, end = m.group(1), m.group(2), m.group(3).strip(), m.group(4)
        if content:
            segments.append({
                "start": float(start),
                "end": float(end),
                "speaker": speaker,
                "text": content,
            })
    return segments


class Req(BaseModel):
    wav: str


@app.get("/health")
def health():
    return {
        "ok": _model is not None,
        "model": "MOSS-Transcribe-Diarize-0.9B",
        "device": "cuda" if (_model is not None and next(_model.parameters()).is_cuda) else "cpu",
        "vram_gb": round(torch.cuda.memory_allocated() / 1024**3, 2) if torch.cuda.is_available() else 0,
    }


@app.post("/transcribe_diarize")
def transcribe_diarize(req: Req):
    t0 = time.time()
    try:
        model, proc = get_model()
        from transformers.audio_utils import load_audio
        sr = proc.feature_extractor.sampling_rate
        audio = load_audio(req.wav, sampling_rate=sr)
        dur = len(audio) / sr
        messages = [{"role": "user", "content": [
            {"type": "audio", "audio": req.wav},
            {"type": "text", "text": PROMPT},
        ]}]
        text = proc.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = proc(text=text, audio=[audio], return_tensors="pt").to(model.device)
        prompt_len = inputs["input_ids"].shape[1]
        # 长音频需要更多 token
        max_new = max(2048, int(dur * 80))
        with torch.inference_mode(), torch.amp.autocast("cuda", dtype=torch.bfloat16):
            out = model.generate(**inputs, max_new_tokens=max_new, do_sample=False)
        gen = out[0][prompt_len:]
        decoded = proc.batch_decode([gen], skip_special_tokens=False)[0]
        segments = parse_moss_output(decoded)
        # 释放推理显存碎片
        torch.cuda.empty_cache()
        return {
            "segments": segments,
            "duration_s": round(dur, 2),
            "process_time_s": round(time.time() - t0, 2),
        }
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": str(e)[-2000:]}, status_code=500)


if __name__ == "__main__":
    get_model()  # 预加载
    uvicorn.run(app, host="0.0.0.0", port=10100)
