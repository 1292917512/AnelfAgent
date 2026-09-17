"""核心音频 AI 工具面测试：说话人解析 / 错误归因 / 阈值调整 / 统计。"""

from __future__ import annotations

import json

import pytest

import agent.audio.tools as tools_mod
from agent.audio import matcher


def vec(dim: int) -> list[float]:
    return [1.0 if i == dim else 0.0 for i in range(192)]


@pytest.fixture
def store(_isolate_audio_library):
    """核心音频库（conftest 隔离的临时单例）。"""
    return _isolate_audio_library


class TestTools:
    async def test_speaker_update_confirm_flow(self, store) -> None:
        tmp = (await matcher.identify(store, vec(2)))["speaker"]
        raw = await tools_mod.speaker_update(str(tmp["id"]), name="王五", status="confirmed")
        body = json.loads(raw)
        assert body["speaker"]["name"] == "王五"
        assert body["speaker"]["status"] == "confirmed"

    async def test_resolve_ambiguous_returns_candidates(self, store) -> None:
        await matcher.enroll(store, "张伟", vec(0))
        await matcher.enroll(store, "张强", vec(1))
        raw = await tools_mod.speaker_get("张")
        body = json.loads(raw)
        assert "error" in body
        assert len(body["candidates"]) == 2

    async def test_not_found_error_attribution(self, store) -> None:
        raw = await tools_mod.speaker_get("不存在")
        body = json.loads(raw)
        assert body["cause"] == "not_found"

    async def test_transcript_search_bad_time(self, store) -> None:
        raw = await tools_mod.transcript_search(query="", time_from="垃圾时间")
        body = json.loads(raw)
        assert body["cause"] == "param"

    async def test_set_threshold(self, store) -> None:
        raw = await tools_mod.audio_set_threshold(0.8)
        assert json.loads(raw)["match_threshold"] == 0.8
        assert matcher.global_threshold() == 0.8
        raw = await tools_mod.audio_set_threshold(1.5)
        assert "error" in json.loads(raw)

    async def test_speaker_list_and_stats(self, store) -> None:
        await matcher.enroll(store, "张三", vec(0))
        body = json.loads(await tools_mod.speaker_list())
        assert body["total"] == 1
        stats = json.loads(await tools_mod.audio_stats())
        assert stats["speakers"] == 1

    async def test_speaker_bind_tool(self, store) -> None:
        speaker = await matcher.enroll(store, "张三", vec(0))
        raw = await tools_mod.speaker_bind(str(speaker["id"]), "user:webui:u1")
        body = json.loads(raw)
        assert body["bound"] is True
        assert body["speaker"]["entity_scope"] == "user:webui:u1"
        raw = await tools_mod.speaker_bind(str(speaker["id"]), "bogus")
        assert "error" in json.loads(raw)
        raw = await tools_mod.speaker_bind(str(speaker["id"]), "")
        assert json.loads(raw)["bound"] is False


class TestSpeakerRefineTool:
    async def test_refine_reports_samples_and_drift(self, store) -> None:
        import math

        speaker = await matcher.enroll(store, "张三", vec(3))
        body = json.loads(await tools_mod.speaker_refine("张三"))
        assert body["samples"] == 1
        # 池未变：重建锚 ≈ 自动折叠锚（漂移 ~1）
        assert body["anchor_similarity"] == pytest.approx(1.0, abs=1e-3)
        # 加入偏离样本后剔除原样本：重建不再记忆已删样本（漂移落下）
        tilted = vec(3).copy()
        tilted[3] = 0.8
        tilted[4] = math.sqrt(1 - 0.8 * 0.8)
        sid = int(speaker["id"])
        await store.add_sample(sid, tilted, duration_ms=4000)
        for sample in await store.list_samples(sid):
            if sample["duration_ms"] == 0:
                await store.delete_sample(int(sample["id"]))
        body = json.loads(await tools_mod.speaker_refine("张三"))
        assert body["samples"] == 1
        assert body["anchor_similarity"] < 0.999

    async def test_refine_empty_pool_param_error(self, store) -> None:
        speaker = await store.create_speaker(name="空池")
        raw = await tools_mod.speaker_refine(str(speaker["id"]))
        body = json.loads(raw)
        assert "error" in body and body.get("cause") == "param"

    async def test_refine_unknown_speaker(self, store) -> None:
        raw = await tools_mod.speaker_refine("不存在的人")
        assert "error" in json.loads(raw)


class TestSpeakerCompareTool:
    async def test_compare_tool_reports_criteria(self, store) -> None:
        await matcher.enroll(store, "张三", vec(0))
        await matcher.enroll(store, "分身", vec(1))
        body = json.loads(await tools_mod.speaker_compare("张三", "分身"))
        assert body["anchor_similarity"] == pytest.approx(0.0, abs=1e-6)
        assert "谨慎合并" in body["merge_hint"]

    async def test_compare_unknown_speaker_error(self, store) -> None:
        await matcher.enroll(store, "张三", vec(0))
        body = json.loads(await tools_mod.speaker_compare("张三", "不存在"))
        assert body["cause"] == "not_found"


class TestTranscriptSearchEntityFilter:
    async def test_entity_param_filters_and_validates(self, store) -> None:
        speaker = await matcher.enroll(store, "张三", vec(0))
        await store.bind_entity(int(speaker["id"]), "user:qq:456")
        await store.add_segment(speaker_id=int(speaker["id"]), transcript="张三说吃饭")
        await store.add_segment(transcript="路人说开会")

        body = json.loads(await tools_mod.transcript_search(
            query="", entity="user:qq:456"))
        assert [i["transcript"] for i in body["items"]] == ["张三说吃饭"]

        bad = json.loads(await tools_mod.transcript_search(query="", entity="bogus"))
        assert bad["cause"] == "param"
