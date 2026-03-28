# Workflows

This document tracks the current mode names, routing rules, and workflow runbooks that are actually wired in Float today. Keep it aligned with `backend/app/config.py`, `backend/app/base_services.py`, `backend/app/local_providers/`, `backend/app/services/livekit_service.py`, and the Settings/TopBar mode selectors.

## Mode terminology

- `Cloud API` = backend mode `api`. Chat is sent to the configured OpenAI-compatible API endpoint (`api_url`), which defaults to OpenAI Responses.
- `Local (on-device)` = backend mode `local`. Chat can use a direct local Transformers checkpoint or a managed local provider selected via the runtime marker models `lmstudio` and `ollama`.
- `Server/LAN` = backend mode `server`. Chat is sent to a user-supplied OpenAI-compatible endpoint (`server_url`) without using the local provider manager.
- `dynamic` remains only as a legacy compatibility path in backend code. Do not treat it as a current user-facing mode.

## Provider routing matrix

| Capability | Cloud API | Local (on-device) | Server/LAN | Notes |
| --- | --- | --- | --- | --- |
| Chat + tools | `LLMService(mode="api")` against `api_url` / `api_key` | `LLMService(mode="local")` with either direct Transformers checkpoints or provider-managed LM Studio/Ollama when the selected local model is `lmstudio` or `ollama` | `LLMService(mode="server")` against `server_url` | The mode selector controls chat routing; tool approvals and persistence stay shared. Raw GGUF weights belong behind LM Studio, Ollama, or another OpenAI-compatible server rather than direct Transformers loading. |
| Managed runtime control | n/a | Provider manager can start/stop/load/unload LM Studio or Ollama and expose logs/status via `/llm/provider/*` | n/a | `local_provider_mode` is `local-managed` or `remote-unmanaged`. |
| Vision chat inputs | Native image parts for vision-capable API models; otherwise backend local-caption fallback | Native image parts for multimodal local-compatible models when the transport can handle them; otherwise backend local-caption fallback | Same native-image-vs-caption decision as chat, but sent to `server_url` | Native-image support is inferred from model naming hints; fallback uses `vision_model`. |
| Text embeddings | `rag_embedding_model=api:*` uses the configured API base/key | `rag_embedding_model=local:*` uses local Sentence Transformers; `simple` hash fallback remains available | No separate Server/LAN embedding transport today | Do not imply embeddings automatically follow `server_url`; current API embeddings use `api_url`. |
| Image retrieval / CLIP | n/a | Local CLIP indexing via `rag_clip_model` | n/a | This is for retrieval/indexing, not chat image upload transport. |
| RAG retrieval | Shared SQLite + Chroma-backed retrieval available | Same | Same | Retrieval is mode-agnostic once data is indexed. |
| TTS | OpenAI `/v1/audio/speech` for `tts-1` / `tts-1-hd` | Local `kitten`, `kokoro`, or other local TTS checkpoints through `TTSService` | No dedicated Server/LAN TTS bridge today | Voice presets are model-specific in Settings. |
| STT / transcription | OpenAI `/v1/audio/transcriptions` for the current voice-worker path | Local Whisper is used today for semantic audio tagging/embeddings, not as the main chat STT path | No dedicated Server/LAN STT bridge today | Keep this distinction explicit in docs and UI copy. |
| Live streaming transport | OpenAI Realtime client-secret bootstrap plus browser WebRTC when `FLOAT_STREAM_BACKEND=api` | LiveKit fallback path remains available if configured | LiveKit can still bridge to a remote Float instance, but this is not the same as generic OpenAI-compatible server mode | `/api/voice/connect` is provider-aware; `/voice/stream` is only for the non-Realtime worker path. |

## Chat runbook

- Use `Cloud API` when you want first-party API chat, OpenAI-compatible API chat, or OpenAI-hosted speech/TTS.
- Use `Local (on-device)` when you want either:
  - a direct local Transformers checkpoint such as `gpt-oss-20b`, or
  - a managed provider runtime by selecting the local marker `lmstudio` or `ollama`.
- Use `Server/LAN` only for an already-running OpenAI-compatible server that Float should talk to over HTTP. This mode does not manage that server process for you.
- Attachments persist under `data/files/...`, and chat history persists under `data/conversations/`.
- Vision chat should prefer native image input when the selected model truly supports it; otherwise rely on the local caption fallback and surface that fact in metadata/UI.

## Live streaming runbook

- Current cloud-default live streaming path is OpenAI Realtime, not LiveKit-only.
- `FLOAT_STREAM_BACKEND=api` makes `/api/voice/connect` mint an OpenAI Realtime client secret and return the browser-facing `/v1/realtime/calls` connect URL.
- The frontend then establishes `RTCPeerConnection` directly to OpenAI using the ephemeral client secret.
- `FLOAT_STREAM_BACKEND=livekit` (or other non-`api` values) keeps the older LiveKit room/token flow.
- `/api/voice/stream` is intentionally disabled for Realtime API mode because the browser streams directly to OpenAI in that path.
- Live browser verification is still required for microphone permissions, turn-taking, interruption handling, and transcript/event surfacing decisions.

## Background workflows

These are still useful concepts, but they should be described in current runtime terms rather than the retired internal `llama.cpp` path.

### Tool continuation / follow-up work

- Use `/chat/continue` when a tool batch resolves and the assistant needs to synthesize a next step.
- Keep tool outcomes visible in chat or the Agent Console; do not assume silent background completion.
- When documenting prompt behavior, mention `tool_help` and `tool_info` because the base prompt now expects those discovery handles.

### Knowledge refresh / reindex

- SQLite is the canonical knowledge store; Chroma is the retrieval mirror.
- Reindex flows should describe which data types are affected (`memory`, `document`, `calendar_event`, attachments) and whether the run also updates vector mirrors.
- CLIP image indexing is separate from text embeddings and should be documented separately when relevant.

### Delegated/background tasks

- Scheduled follow-ups and `create_task` share the same action payload shape.
- Conversation routing for scheduled actions can target the current chat or open a new chat; docs should call that out explicitly when describing review flows.
- Background task docs should refer to the Agent Console and scheduled runner, not a generic future worker panel unless that UI exists.

## Documentation guardrails

- Prefer the mode names shown in the UI: `Cloud API`, `Local (on-device)`, and `Server/LAN`.
- When a capability is not wired to `server_url` today, say so explicitly instead of implying parity with API mode.
- When documenting GGUF-backed models, describe the transport as LM Studio, Ollama, or another OpenAI-compatible server. Do not imply Float loads raw `.gguf` files directly through the local Transformers path.
- When a path depends on model naming heuristics or local fallback behavior, document the fallback.
- If a later change adds custom HTTP/MCP tools, managed server-mode speech, or provider-backed embeddings, update this document at the same time.
