from pipelines import PipelineRouter


def test_router_selects_voice_chat_pipeline():
    router = PipelineRouter()
    pipe = router.route({"audio", "tts"})
    assert pipe is not None
    assert pipe.name == "voice_chat_full_duplex"


def test_router_respects_output_flags():
    router = PipelineRouter()
    pipe = router.route({"image"}, outputs={"image_caption"})
    assert pipe is not None
    assert pipe.name == "image_caption"


def test_router_fallback_to_modular_stack():
    router = PipelineRouter()
    router.set_available("multimodal_all_in_one", False)
    pipe = router.route({"text", "image"})
    assert pipe is not None
    assert pipe.name == "image_plus_text"
