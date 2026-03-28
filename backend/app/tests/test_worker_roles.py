from workers.multimodal import (ASR, ASR_CACHE, ROLE_REGISTRY, VISION_CACHE,
                                VisionCaptioner)


def test_asr_and_vad_are_edge():
    assert ROLE_REGISTRY["ASR"].location == "edge"
    assert ROLE_REGISTRY["VAD"].location == "edge"
    assert ROLE_REGISTRY["LLM"].location == "central"


def test_caches_are_used_for_repeated_inputs():
    img = b"dummy-image"
    cap_worker = VisionCaptioner()
    first = cap_worker.run(img)
    second = cap_worker.run(img)
    assert first == second
    assert len(VISION_CACHE) == 1

    audio = b"dummy-audio"
    asr_worker = ASR()
    first_asr = asr_worker.run(audio)
    second_asr = asr_worker.run(audio)
    assert first_asr == second_asr
    assert len(ASR_CACHE) == 1
