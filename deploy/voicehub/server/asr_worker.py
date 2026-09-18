"""SenseVoice-Small ASR worker（逐 turn 转写，含情绪/事件标签）。端口 10099。"""
import os
os.environ["MODELSCOPE_CACHE"] = r"D:\ServicesCenter\voicehub\models\_cache"
import re
import torch
import numpy as np
import soundfile as sf
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from funasr import AutoModel
import uvicorn

app = FastAPI()
_sv = None
MODEL = r"D:\ServicesCenter\voicehub\models\sensevoice"
_TAG_RE = re.compile(r"<\|([^|]+)\|>")
_EMOTIONS = {"HAPPY", "SAD", "ANGRY", "NEUTRAL", "FEARFUL", "DISGUSTED", "SURPRISED"}
_EVENTS = {"Laughter", "Applause", "BGM", "Cry", "Cough", "Breathing", "Sneeze", "Singing"}


def get_sv():
    global _sv
    if _sv is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _sv = AutoModel(model=MODEL, device=device, disable_update=True)
        print("[asr] SenseVoice loaded device=%s" % device, flush=True)
    return _sv


def parse_tags(raw):
    raw = str(raw or "")
    tags = _TAG_RE.findall(raw)
    text = _TAG_RE.sub("", raw).strip()
    emotion, events = None, []
    for t in tags:
        t = t.strip()
        if t.upper() in _EMOTIONS:
            emotion = t.upper()
        elif t in _EVENTS:
            events.append(t)
    return text, emotion, events


@app.get("/health")
def health():
    return {"ok": True, "cuda": torch.cuda.is_available()}


@app.post("/asr")
async def asr(req: dict):
    wav = req["wav"]
    turns = req.get("turns") or []
    sv = get_sv()
    audio, sr = sf.read(wav, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if not turns:
        turns = [{"start": 0.0, "end": len(audio) / sr, "speaker": "S0"}]
    results = []
    for t in turns:
        s = int(t["start"] * sr)
        e = int(t["end"] * sr)
        seg = audio[s:e]
        text, emo, evs = "", None, []
        if len(seg) >= int(0.1 * sr):
            try:
                r = sv.generate(input=seg, cache={}, language="zh", use_itn=True)
                if r:
                    text, emo, evs = parse_tags(r[0].get("text", ""))
            except Exception as ex:
                print("[asr] seg fail:", ex, flush=True)
        results.append({"start": t["start"], "end": t["end"], "speaker": t.get("speaker"),
                        "text": text, "emotion": emo, "events": evs})
    return JSONResponse({"results": results})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=10099)
