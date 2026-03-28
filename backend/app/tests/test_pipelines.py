import pytest
from pipelines import PipelineRouter


@pytest.mark.parametrize(
    "needs,name",
    [
        ({"text"}, "text_only"),
        ({"text", "image"}, "image_plus_text"),
        ({"audio"}, "audio_streaming"),
        ({"video", "audio"}, "video_streaming"),
        ({"audio", "tts"}, "voice_chat_full_duplex"),
    ],
)
def test_router_selects_expected_pipeline(needs, name):
    router = PipelineRouter()
    pipe = router.route(needs)
    assert pipe is not None
    assert pipe.name == name


def test_router_falls_back_to_multimodal_pipeline():
    router = PipelineRouter()
    pipe = router.route({"image"})
    assert pipe is not None
    assert pipe.name == "multimodal_all_in_one"


@pytest.mark.parametrize(
    "needs,inputs,expected",
    [
        ({"text"}, {"text": "hi"}, {"text"}),
        (
            {"text", "image"},
            {"text": "hi", "image": b"img"},
            {"image_caption"},
        ),
        (
            {"audio"},
            {"audio": b"aud"},
            {"speech_turn", "asr"},
        ),
        (
            {"video", "audio"},
            {"video": b"vid", "audio": b"aud"},
            {"keyframe", "image_caption", "speech_turn", "asr"},
        ),
        ({"audio", "tts"}, {"audio": b"aud"}, {"tts"}),
        ({"image"}, {}, {"text"}),
    ],
)
def test_pipeline_execution(needs, inputs, expected):
    router = PipelineRouter()
    pipe = router.route(needs)
    assert pipe is not None
    result = pipe.run(dict(inputs))
    for key in expected:
        assert key in result
