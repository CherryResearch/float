import pytest
from workers.multimodal import (
    ASR,
    ASR_CACHE,
    ROLE_REGISTRY,
    VISION_CACHE,
    VisionCaptioner,
)


@pytest.fixture(autouse=True)
def clear_worker_caches():
    VISION_CACHE.clear()
    ASR_CACHE.clear()
    yield
    VISION_CACHE.clear()
    ASR_CACHE.clear()


def test_asr_and_vad_are_edge():
    assert ROLE_REGISTRY["ASR"].location == "edge"
    assert ROLE_REGISTRY["VAD"].location == "edge"
    assert ROLE_REGISTRY["LLM"].location == "central"


def test_real_vision_captions_are_cached_by_model_and_content(monkeypatch):
    img = b"dummy-image"
    cap_worker = VisionCaptioner()
    calls = []
    monkeypatch.setattr(cap_worker, "_load_if_possible", lambda: None)

    def caption(_data):
        calls.append(True)
        return "A real image caption."

    monkeypatch.setattr(cap_worker, "_caption_with_model", caption)
    first = cap_worker.run(img)
    second = cap_worker.run(img)
    assert first == second
    assert calls == [True]
    assert len(VISION_CACHE) == 1


def test_placeholder_is_not_cached_and_same_bytes_can_retry(monkeypatch):
    img = b"retry-image"
    cap_worker = VisionCaptioner()
    load_attempts = []

    def load():
        load_attempts.append(True)
        if len(load_attempts) == 2:
            cap_worker._loaded = True
            cap_worker._proc = object()
            cap_worker._net = object()

    monkeypatch.setattr(cap_worker, "_load_if_possible", load)
    monkeypatch.setattr(
        cap_worker,
        "_caption_with_model",
        lambda _data: "A recovered image caption." if cap_worker._loaded else None,
    )

    first = cap_worker.run(img)
    assert first.startswith("[placeholder]")
    assert VISION_CACHE == {}

    second = cap_worker.run(img)
    assert second == "A recovered image caption."
    assert len(load_attempts) == 2
    assert len(VISION_CACHE) == 1


def test_audio_cache_is_used_for_repeated_inputs():
    audio = b"dummy-audio"

    asr_worker = ASR()
    first_asr = asr_worker.run(audio)
    second_asr = asr_worker.run(audio)
    assert first_asr == second_asr
    assert len(ASR_CACHE) == 1
