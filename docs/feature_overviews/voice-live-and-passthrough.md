voice, live mode, and passthrough are the features that move float beyond ordinary typed chat. Voice mode is about speaking with float more naturally, live mode is about keeping that interaction going in real time, and passthrough is the broader idea of float staying present beside a live desktop or camera view.

For the user, this is the beginning of a more ambient assistant. Instead of opening a chat box only when something needs to be typed, float can eventually stay present during another activity, listen, respond, and help keep context together while the user is focused elsewhere. That is why this feature area matters even when some parts are still early.

The current cloud voice path is OpenAI Realtime through `/api/voice/connect`, with LiveKit kept as a fallback transport and Pipecat still being explored as a future pipeline option. Gemma 4 is not a supported live-mode transport in this pass; Gemma belongs in the local/server language-model lanes for text and still-image work, while live audio remains on the Realtime/LiveKit track.

The current voice path is real enough to use and improve, but live sessions and passthrough-style experiences are still less mature than core text chat. The public references for the current shipped surface are `README.md`, `docs/environment setup.md`, `docs/api_reference.md`, and this overview.
