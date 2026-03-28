"""Test turn detection and response logic."""

# isort: skip_file

import pytest
import asyncio
from pathlib import Path
import sys


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(backend_dir))
    yield
    sys.path.remove(str(backend_dir))


class DummyThinkLLM:
    def generate(self, prompt, **_):
        return {"thought": f"t:{prompt}"}


class DummyResponseLLM:
    def __init__(self):
        self.prompts = []

    def generate(self, messages, **_):
        self.prompts.append(messages)
        # respond with the user transcript for simplicity
        return {"text": f"resp:{messages[0].content[0].text}"}


@pytest.mark.asyncio
async def test_turn_detection_threshold():
    from audio.turn_detector import TurnDetector

    detector = TurnDetector(DummyThinkLLM(), silence_threshold=2)

    await detector.process_fragment("hello")
    await detector.process_fragment("world")
    assert detector.events.empty()
    await detector.process_fragment("")
    assert detector.events.empty()
    await detector.process_fragment("")
    event = await asyncio.wait_for(detector.events.get(), 0.1)
    assert event["type"] == "turn_complete"
    assert event["transcript"] == "hello world"
    assert "t:hello" in event["thought"]


@pytest.mark.asyncio
async def test_response_worker_triggers_on_event():
    from audio.turn_detector import TurnDetector
    from workers.response_worker import ResponseWorker

    detector = TurnDetector(DummyThinkLLM(), silence_threshold=1)
    worker = ResponseWorker(detector.events, DummyResponseLLM())
    task = asyncio.create_task(worker.run())

    try:
        await detector.process_fragment("hi there")
        await detector.process_fragment("")
        result = await asyncio.wait_for(worker.response_queue.get(), 0.1)
        assert result["text"].startswith("resp:")
        # Ensure transcript and thought were part of prompt
        used_messages = worker.llm.prompts[0]
        assert used_messages[0].content[0].text == "hi there"
        assert used_messages[1].channel == "analysis"
        assert used_messages[1].content[0].text == "t:hi there"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
