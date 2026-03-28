import asyncio

from app.base_services import LLMService
from app.utils.harmony import Message, Role


class ResponseWorker:
    """Generate assistant responses for completed turns.

    The worker listens on ``event_queue`` for ``turn_complete`` events
    produced by :class:`audio.turn_detector.TurnDetector`. When such an
    event is received the accumulated transcript and chain-of-thought are
    sent to a response LLM and the result is published to
    :attr:`response_queue` for the frontend to consume.
    """

    def __init__(
        self,
        event_queue: asyncio.Queue,
        llm: LLMService,
        response_format: str | None = None,
    ):
        self.event_queue = event_queue
        self.llm = llm
        self.response_format = response_format
        self.response_queue: asyncio.Queue = asyncio.Queue()

    async def run(self) -> None:
        """Continuously process turn-complete events."""
        while True:
            event = await self.event_queue.get()
            if event.get("type") != "turn_complete":
                continue
            transcript = event.get("transcript", "")
            thought = event.get("thought", "")
            messages = [
                Message.from_role_and_content(Role.USER, transcript),
                Message.from_role_and_content(Role.USER, thought).with_channel(
                    "analysis"
                ),
            ]
            result = await asyncio.to_thread(
                self.llm.generate,
                messages,
                response_format=self.response_format,
            )
            await self.response_queue.put(result)
