"""独立声纹模块（ONNX Runtime / sherpa-onnx）——与 FunASR 解耦。
主模型 ERes2NetV2，加载失败回退 CAM++。输出 192 维向量。
sherpa-onnx 1.13.x API：SpeakerEmbeddingExtractor(SpeakerEmbeddingExtractorConfig(model=...))"""
import numpy as np
import sherpa_onnx
import soundfile as sf


class Embedder:
    def __init__(self, model_path):
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model_path)
        self.ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self.dim = int(self.ex.dim)

    def embed(self, wav_path, start=None, end=None):
        audio, sr = sf.read(wav_path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if start is not None and end is not None:
            s = max(0, int(start * sr))
            e = min(len(audio), int(end * sr))
            audio = audio[s:e]
        if len(audio) < int(0.3 * sr):
            return None
        stream = self.ex.create_stream()
        stream.accept_waveform(sr, audio)
        stream.input_finished()
        if not self.ex.is_ready(stream):
            return None
        vec = self.ex.compute(stream)
        return np.array(vec, dtype=np.float32)
