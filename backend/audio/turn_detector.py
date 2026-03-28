import asyncio
from typing import List

from app.base_services import LLMService

try:  # pragma: no cover - livekit is optional
    from livekit.agents import llm as lk_llm
    from livekit.plugins.turn_detector import EnglishModel
except Exception:  # pragma: no cover - ignore if livekit not installed
    EnglishModel = None
    lk_llm = None


class TurnDetector:
    """Detect conversation turns from transcript fragments.

    By default this leverages the LiveKit end-of-utterance model if the
    ``livekit`` packages are installed. When unavailable, a simple
    silence-counter fallback is used. Each fragment is forwarded to a
    "thinking" LLM to accumulate chain-of-thought. When an end of turn is
    detected, a ``turn_complete`` event is emitted with the transcript and
    collected thoughts. The `smart-turn`_ project can also serve as an
    alternative detector.

    .. _smart-turn: https://github.com/pipecat-ai/smart-turn
    """

    def __init__(
        self,
        llm: LLMService,
        silence_threshold: int = 2,
        eou_threshold: float = 0.5,
    ) -> None:
        self.llm = llm
        self.fragments: List[str] = []
        self.thoughts: List[str] = []
        self.events: asyncio.Queue = asyncio.Queue()

        if EnglishModel and lk_llm:
            self._use_livekit = True
            self._model = EnglishModel()
            self._chat_ctx = lk_llm.ChatContext()
            self._eou_threshold = eou_threshold
        else:
            self._use_livekit = False
            self.silence_threshold = silence_threshold
            self.silence_count = 0

    async def process_fragment(self, fragment: str) -> None:
        """Process a single transcript fragment."""

        if fragment and fragment.strip():
            cleaned = fragment.strip()
            self.fragments.append(cleaned)
            thought = await asyncio.to_thread(self.llm.generate, fragment)
            self.thoughts.append(thought.get("thought", ""))

            if self._use_livekit:
                msg = lk_llm.ChatMessage(role="user", content=cleaned)
                self._chat_ctx.append(msg)
                prob = await self._model.predict_end_of_turn(self._chat_ctx)
                if prob >= self._eou_threshold:
                    await self._emit_turn_complete()
            else:
                self.silence_count = 0
        else:
            if not self._use_livekit:
                self.silence_count += 1
                if self.silence_count >= self.silence_threshold:
                    await self._emit_turn_complete()

    async def _emit_turn_complete(self) -> None:
        transcript = " ".join(self.fragments).strip()
        chain = "\n".join(t for t in self.thoughts if t)
        event = {
            "type": "turn_complete",
            "transcript": transcript,
            "thought": chain,
        }
        await self.events.put(event)
        self.fragments.clear()
        self.thoughts.clear()
        if not self._use_livekit:
            self.silence_count = 0
