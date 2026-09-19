"""pyannote community-1 分离 worker（VBx+PLDA，exclusive 对齐时间戳）。端口 10098。

音频读取：soundfile 预解码为 waveform 字典喂给 pipeline，绕开 torchcodec
（torchcodec 0.16 只认 FFmpeg 4-7 DLL，本机是 FFmpeg 8/9，会 OSError）。
pyannote.audio.core.io 明确支持 {"waveform": Tensor(ch,time), "sample_rate": int}。
非 wav 格式（m4a/mp3/ogg）先用 ffmpeg CLI 转 16k mono wav。
"""
import os
import subprocess
import tempfile

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pyannote.audio import Pipeline

app = FastAPI()
_pipe = None
CFG = r"D:\ServicesCenter\voicehub\models\community-1\config.yaml"
FFMPEG = r"D:\ServicesCenter\tools\ffmpeg\bin\ffmpeg.exe"


def get_pipe():
    global _pipe
    if _pipe is None:
        _pipe = Pipeline.from_pretrained(CFG)
        if _pipe is None:
            raise RuntimeError("pyannote pipeline load failed")
        if torch.cuda.is_available():
            _pipe.to(torch.device("cuda"))
        print("[diarize] pipeline loaded, cuda=%s" % torch.cuda.is_available(), flush=True)
    return _pipe


def load_waveform(path):
    """读音频 -> (torch.Tensor[1, T] float32, 16000)。非 wav 先 ffmpeg 转码。"""
    ext = os.path.splitext(path)[1].lower()
    src = path
    tmp = None
    if ext not in (".wav", ".flac"):
        fd, tmp = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        r = subprocess.run([FFMPEG, "-y", "-i", path, "-ar", "16000", "-ac", "1", "-f", "wav", tmp],
                           capture_output=True)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg convert failed: %s" % r.stderr.decode(errors="replace")[-500:])
        src = tmp
    audio, sr = sf.read(src, dtype="float32")
    if tmp:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    wav = torch.from_numpy(audio).unsqueeze(0)  # (1, T)
    return {"waveform": wav, "sample_rate": sr}


@app.get("/health")
def health():
    return {"ok": True, "cuda": torch.cuda.is_available(), "pipeline": _pipe is not None}


@app.post("/diarize")
async def diarize(req: dict):
    wav_path = req["wav"]
    ns = req.get("num_speakers")
    p = get_pipe()
    audio = load_waveform(wav_path)
    kwargs = {}
    if ns:
        kwargs["num_speakers"] = ns
    out = p(audio, **kwargs)
    turns = []
    if hasattr(out, "serialize"):
        data = out.serialize()
        turns = data.get("exclusive_diarization") or data.get("diarization") or []
    elif hasattr(out, "itertracks"):
        for turn, _, spk in out.itertracks(yield_label=True):
            turns.append({"start": round(turn.start, 3), "end": round(turn.end, 3), "speaker": spk})
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return JSONResponse({"turns": turns})


@app.get("/memstat")
def memstat():
    if not torch.cuda.is_available():
        return {"cuda": False}
    return {"cuda": True,
            "allocated_mb": round(torch.cuda.memory_allocated() / 1048576, 1),
            "reserved_mb": round(torch.cuda.memory_reserved() / 1048576, 1),
            "max_allocated_mb": round(torch.cuda.max_memory_allocated() / 1048576, 1)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=10098)
