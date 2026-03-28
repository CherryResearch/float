# Live Mode Architecture

## Turn Detection Flow
- Transcript fragments are streamed into the `TurnDetector`.
- When LiveKit packages are available, it uses the English end-of-utterance model to predict when a speaker has finished. Otherwise, a silence counter fallback is used.
- Each fragment is also sent to a "thinking" LLM to accumulate chain-of-thought text.
- When an end of turn is detected, a `turn_complete` event with the transcript and thoughts is published to a queue for downstream workers.

## Video Snapshot Logic
- The `KeyframeDetector` samples frames from a video stream, scoring them for novelty against the previous frame.
- Frames that exceed a capture threshold are saved and optionally escalated to an LLM context via a callback.
- A high-entropy video model can be plugged in to score frames, but it remains experimental.

## Worker Interactions
- A `ResponseWorker` listens for `turn_complete` events and generates replies using a response LLM.
- Additional workers can post custom events (e.g., screenshot triggers) to `/live/worker-event`, allowing Pipecat's pipeline to incorporate future workers.
- Workers may receive partial context (e.g. an image and snippet of transcribed text) to 'think about' but not respond, which gets concatenated in the context to the response worker.

## Provider Configuration
- The streaming provider is selected via the `provider` field in the model catalog YAML.
- `provider: pipecat` uses the Pipecat pipeline, while `provider: livekit` uses LiveKit.
- Set `MODEL_CATALOG_PATH` to point to one of the example configs when launching the server.

## Demo Sessions
1. **Pipecat**
   ```bash
   export MODEL_CATALOG_PATH=examples/pipecat_config.yaml
   poetry run uvicorn app.main:app --app-dir backend --reload
   # Negotiate a session
   curl -X POST http://localhost:8000/live/session
   ```
2. **LiveKit**
   ```bash
   export MODEL_CATALOG_PATH=examples/livekit_config.yaml
   poetry run uvicorn app.main:app --app-dir backend --reload
   curl -X POST http://localhost:8000/live/session
   ```
- Connect a WebSocket client to `ws://localhost:8000/live/ws` to stream audio and receive responses.
- Uploaded images can be sent to `/live/image`, and worker events to `/live/worker-event`.

## Experimental Components
- The keyframe detection by detection of entropy changes model used for snapshot scoring is experimental and may change.
- Pipecat's modular pipeline is designed to accommodate future workers, making it easy to insert additional audio, video, or tool-processing components as they become available. This is being evaluated against and alongside livekit.
