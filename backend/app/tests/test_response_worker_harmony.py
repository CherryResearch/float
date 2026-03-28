import asyncio

import pytest
from app.utils.harmony import Role
from workers.response_worker import ResponseWorker


class DummyHarmonyLLM:
    def __init__(self):
        self.calls = []

    def generate(self, messages, response_format=None, **_):
        self.calls.append(
            {
                "messages": messages,
                "response_format": response_format,
            }
        )
        return {"text": [{"type": "output_text", "text": "resp"}]}


@pytest.mark.asyncio
async def test_response_worker_preserves_harmony_format():
    event_queue = asyncio.Queue()
    llm = DummyHarmonyLLM()
    worker = ResponseWorker(event_queue, llm, response_format="harmony")
    task = asyncio.create_task(worker.run())
    try:
        await event_queue.put(
            {
                "type": "turn_complete",
                "transcript": "hi there",
                "thought": "tada",
            }
        )
        result = await asyncio.wait_for(worker.response_queue.get(), 0.1)
        msgs = llm.calls[0]["messages"]
        assert msgs[0].author.role == Role.USER
        assert msgs[0].content[0].text == "hi there"
        assert msgs[1].channel == "analysis"
        assert msgs[1].content[0].text == "tada"
        assert llm.calls[0]["response_format"] == "harmony"
        assert result["text"] == [{"type": "output_text", "text": "resp"}]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
