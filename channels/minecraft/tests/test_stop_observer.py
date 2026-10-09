from __future__ import annotations

from channels.minecraft.stop_observer import StopCandidateLedger, classify_unmatched_stop


def test_unmatched_stop_classification_preserves_negation_and_composite() -> None:
    assert classify_unmatched_stop("别停，继续走") == "negation"
    assert classify_unmatched_stop("停下然后跟我走") == "composite"
    assert classify_unmatched_stop("帮我看看背包") is None


def test_candidate_resolution_returns_request_and_elapsed() -> None:
    ledger = StopCandidateLedger()
    ledger.record("mc-12", "停一下之后再说", "composite")
    result = ledger.resolve("mc-12", request_id="request-1", tool="cancel_task", stopped=False)
    assert result is not None
    assert result["message_id"] == "mc-12"
    assert result["request_id"] == "request-1"
    assert result["tool"] == "cancel_task"
    assert result["stopped"] is False
    assert isinstance(result["elapsed_ms"], float)
    assert ledger.resolve("mc-12", request_id="request-2", tool="cancel_task", stopped=True) is None
