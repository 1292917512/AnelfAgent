"""端点检测测试：能量法事件序列 / silero v6 判段（mock 推理）/ 工厂梯队降级。"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from agent.voice import turn_detection as td
from agent.voice.turn_detection import (
    EnergyTurnDetector,
    TurnEvent,
    create_turn_detector,
)

RATE = 16000


def ConfigManager_set(key: str, value) -> None:
    from core.config import ConfigManager

    ConfigManager.set(key, value)


def pcm_silence(ms: int) -> bytes:
    return b"\x00\x00" * int(RATE * ms / 1000)


def pcm_tone(ms: int, amp: int = 8000) -> bytes:
    """正弦波 PCM16（能量法可识别的稳定语音信号）。"""
    n = int(RATE * ms / 1000)
    frames = bytearray()
    for i in range(n):
        v = int(amp * np.sin(2 * np.pi * 440 * i / RATE))
        frames += v.to_bytes(2, "little", signed=True)
    return bytes(frames)


class TestEnergyTurnDetector:
    def test_silence_no_events(self) -> None:
        det = EnergyTurnDetector(RATE)
        events = [det.accept_pcm(pcm_silence(20)) for _ in range(50)]
        assert set(events) == {TurnEvent.NONE}
        assert not det.in_speech

    def test_start_then_end_sequence(self) -> None:
        det = EnergyTurnDetector(RATE)
        events = [det.accept_pcm(pcm_silence(20)) for _ in range(50)]  # 1s 底噪学习
        events += [det.accept_pcm(pcm_tone(20)) for _ in range(50)]   # 1s 语音
        assert TurnEvent.SPEECH_START in events
        assert det.in_speech
        events = [det.accept_pcm(pcm_silence(20)) for _ in range(60)]  # 1.2s 静音
        assert TurnEvent.SPEECH_END in events
        assert not det.in_speech

    def test_brief_noise_no_start(self) -> None:
        det = EnergyTurnDetector(RATE)
        for _ in range(50):
            det.accept_pcm(pcm_silence(20))
        events = [det.accept_pcm(pcm_tone(20)) for _ in range(3)]  # 60ms 突发
        events += [det.accept_pcm(pcm_silence(20)) for _ in range(50)]
        assert TurnEvent.SPEECH_START not in events

    def test_reset(self) -> None:
        det = EnergyTurnDetector(RATE)
        for _ in range(50):
            det.accept_pcm(pcm_silence(20))
        for _ in range(50):
            det.accept_pcm(pcm_tone(20))
        assert det.in_speech
        det.reset()
        assert not det.in_speech


class _FakeSileroSession:
    """silero v6 推理桩：校验输入契约（input[1,576]/state/sr），吐脚本概率。"""

    def __init__(self, probs: list[float]) -> None:
        self._probs = list(probs)
        self.inputs_seen: list[dict] = []

    def run(self, _names, feeds):
        self.inputs_seen.append(feeds)
        prob = self._probs.pop(0) if self._probs else 0.1
        state = np.asarray(feeds["state"], dtype=np.float32)
        return [[prob]], state + np.float32(1.0)


@pytest.fixture
def fake_onnx(monkeypatch: pytest.MonkeyPatch):
    """注入伪 onnxruntime 模块（SessionOptions/InferenceSession 可空转）。"""
    module = types.ModuleType("onnxruntime")
    module.SessionOptions = lambda: types.SimpleNamespace(
        intra_op_num_threads=0, inter_op_num_threads=0)
    module.ExecutionMode = types.SimpleNamespace(ORT_SEQUENTIAL=0)
    module.GraphOptimizationLevel = types.SimpleNamespace(ORT_ENABLE_ALL=0)
    holder: dict = {}

    def make_session(path, sess_options=None, providers=None):
        return _FakeSileroSession(holder["probs"])

    module.InferenceSession = make_session
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    return holder


class TestSileroTurnDetector:
    def test_v6_io_contract_and_events(self, fake_onnx) -> None:
        fake_onnx["probs"] = [0.9] * 12 + [0.1] * 40
        det = td.SileroTurnDetector("/fake.onnx")
        # 每次喂 960 样本（60ms）= 近 2 窗（512/窗），窗窗推理
        events = [det.accept_pcm(b"\x01\x00" * 960) for _ in range(6)]   # 360ms 语音
        assert TurnEvent.SPEECH_START in events
        assert det.in_speech
        events = [det.accept_pcm(b"\x00\x00" * 960) for _ in range(15)]  # 900ms 静音
        assert TurnEvent.SPEECH_END in events
        assert not det.in_speech
        session = det._session
        assert isinstance(session, _FakeSileroSession)
        feed = session.inputs_seen[0]
        assert feed["input"].shape == (1, td.SileroTurnDetector.WINDOW + td.SileroTurnDetector.CONTEXT)
        assert feed["state"].shape == (2, 1, 128)
        assert feed["sr"].item() == RATE
        # LSTM 状态跨窗传递
        assert not np.array_equal(session.inputs_seen[0]["state"],
                                  session.inputs_seen[1]["state"])

    def test_hysteresis_no_flapping(self, fake_onnx) -> None:
        fake_onnx["probs"] = [0.9] * 12 + [0.42] * 60
        det = td.SileroTurnDetector("/fake.onnx")
        events = [det.accept_pcm(b"\x01\x00" * 960) for _ in range(6)]
        events += [det.accept_pcm(b"\x00\x00" * 960) for _ in range(25)]
        assert TurnEvent.SPEECH_START in events
        assert TurnEvent.SPEECH_END not in events
        assert det.in_speech


class TestFactory:
    @pytest.fixture(autouse=True)
    def _fresh_runtime(self):
        """每用例重置语义端点运行时缓存（防跨用例/宿主机真模型串扰）。"""
        td.reset_smart_turn_runtime()
        yield
        td.reset_smart_turn_runtime()

    def test_auto_without_models_falls_to_energy(self, monkeypatch) -> None:
        from agent.model_assets import get_model_asset_manager

        monkeypatch.setattr(
            get_model_asset_manager(), "resolve", lambda asset_id: None)
        assert isinstance(create_turn_detector(RATE), EnergyTurnDetector)

    def test_silero_without_model_falls_back(self, monkeypatch) -> None:
        from agent.model_assets import get_model_asset_manager

        monkeypatch.setattr(
            get_model_asset_manager(), "resolve", lambda asset_id: None)
        ConfigManager_set("voice_turn_detector", "silero")
        try:
            assert isinstance(create_turn_detector(RATE), EnergyTurnDetector)
        finally:
            ConfigManager_set("voice_turn_detector", "auto")

    def test_silero_with_model(self, fake_onnx, monkeypatch) -> None:
        fake_onnx["probs"] = [0.1] * 100
        from agent.model_assets import get_model_asset_manager

        monkeypatch.setattr(
            get_model_asset_manager(), "resolve",
            lambda asset_id: "/fake/silero_vad.onnx" if asset_id == "silero_vad" else None)
        det = create_turn_detector(RATE)
        assert type(det).__name__ == "SileroTurnDetector"

    def test_smart_turn_with_model_energy_base(self, fake_onnx, monkeypatch) -> None:
        fake_onnx["probs"] = [0.1] * 100

        class _FakeSmart:
            def predict(self, audio):
                return 0.1

        monkeypatch.setattr(td, "_runtime", _FakeSmart())
        from agent.model_assets import get_model_asset_manager

        monkeypatch.setattr(
            get_model_asset_manager(), "resolve",
            lambda asset_id: "/fake.onnx" if asset_id == "smart_turn" else None)
        det = create_turn_detector(RATE)
        assert type(det).__name__ == "SmartTurnTurnDetector"
        # 基座为能量法（silero 资产未就绪）
        assert type(det._base).__name__ == "EnergyTurnDetector"

    def test_smart_turn_without_model_degrades(self, monkeypatch) -> None:
        from agent.model_assets import get_model_asset_manager

        monkeypatch.setattr(td, "_runtime", None)
        monkeypatch.setattr(td, "_runtime_failed", False)
        monkeypatch.setattr(
            get_model_asset_manager(), "resolve", lambda asset_id: None)
        ConfigManager_set("voice_turn_detector", "smart_turn")
        try:
            assert isinstance(create_turn_detector(RATE), EnergyTurnDetector)
        finally:
            ConfigManager_set("voice_turn_detector", "auto")


class TestDetectorStatus:
    def test_status_matches_factory_resolution(self, monkeypatch) -> None:
        from agent.model_assets import get_model_asset_manager

        mgr = get_model_asset_manager()
        monkeypatch.setattr(td, "_runtime_failed", False)
        monkeypatch.setattr(
            mgr, "resolve",
            lambda asset_id: {
                "silero_vad": "/m/silero.onnx", "smart_turn": "/m/smart.onnx",
            }.get(asset_id))
        monkeypatch.setattr(
            "agent.model_assets.runtime_ready", lambda asset: True)
        st = td.detector_status()
        assert st == {
            "configured": "auto", "effective": "smart_turn", "base": "silero",
            "runtime_ready": True,
            "models": {"silero_vad": "ready", "smart_turn": "ready"},
        }

    def test_status_energy_when_no_models(self, monkeypatch) -> None:
        from agent.model_assets import get_model_asset_manager

        monkeypatch.setattr(td, "_runtime_failed", False)
        monkeypatch.setattr(
            get_model_asset_manager(), "resolve", lambda asset_id: None)
        monkeypatch.setattr(
            "agent.model_assets.runtime_ready", lambda asset: False)
        st = td.detector_status()
        assert st["effective"] == "energy"
        assert st["models"]["smart_turn"] == "missing"

    def test_status_pinned_kind_no_cross_fallback(self, monkeypatch) -> None:
        from agent.model_assets import get_model_asset_manager

        mgr = get_model_asset_manager()
        monkeypatch.setattr(td, "_runtime_failed", False)
        monkeypatch.setattr(
            mgr, "resolve",
            lambda asset_id: "/m/silero.onnx" if asset_id == "silero_vad" else None)
        monkeypatch.setattr(
            "agent.model_assets.runtime_ready", lambda asset: True)
        ConfigManager_set("voice_turn_detector", "smart_turn")
        try:
            # 钉死 smart_turn：模型缺失时不跨档借 silero，与工厂一致
            assert td.detector_status()["effective"] == "energy"
        finally:
            ConfigManager_set("voice_turn_detector", "auto")
