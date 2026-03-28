# Chat & Live Input Controls

This specification captures the functional behaviour of the chat composer, attachments, live capture modes, and how they surface in the API. It derives the behaviour highlighted in the UI specification and connects each control to the services that power it.

## Composer layout
- **Primary textarea** expands from 2–3 lines up to 50% of viewport height. Once full, the textarea becomes internally scrollable while the outer chat log remains fixed.
- **Footer buttons** (left → right): attachment launcher, voice record/live toggle, role selector, send button, overflow menu.
- **Header row** (optional) hosts quick filters such as “API” vs “Local”, active workflow, and latency indicator.
- When the composer is collapsed, a pill with the last draft text and a microphone icon remains visible; click or press `⌘/Ctrl + /` to reopen.

## Text entry behaviour
- `Enter` sends the message unless `Shift` is held. Holding `Shift` inserts a newline.
- Drafts are saved to `conversations/<id>/draft.json`. Drafts persist per conversation and per role.
- Mentions (`@role-name`) trigger role suggestions pulled from `/api/roles`.
- Composer surfaces tool suggestions when the user types `/` at the beginning of a line; they are fetched from `/api/tools/catalog`.

## Voice capture and live mode
- Press-and-hold on the microphone starts a **push-to-talk** recording that streams audio chunks to `POST /api/chat/voice-stream`. Releasing finalises the turn, closes the stream, and emits a transcript.
- Toggling **Live mode** locks the microphone icon in an active state, opens a LiveKit/Pipecat session, and displays a waveform meter plus “End Session” button. The frontend negotiates a session via `POST /api/live/session` and streams audio over `ws://<host>/live/ws`.
- Transcripts from live sessions are appended to the active conversation with the `live` modality tag.
- A **Voice replies** toggle lets the assistant speak responses while the mic is active; text input remains available to interrupt or steer the flow without waiting for strict turn boundaries.
- End-of-turn can be explicit (“Finish speaking” action while recording) or inferred after a configurable silence window; show a radial countdown for the final second of the timeout.
- When the mic is active, render a semi-transparent audio-reactive circle with a mic icon. In live mode, show a matching circle with a droplet icon for Float’s speech in the main chat area.

## Attachments
- Clicking the attachment icon opens a picker with three tabs: `Upload`, `Camera`, `From library`.
  - **Upload** writes files to the `object_store/media/uploads/` directory and posts metadata to `POST /api/media`. Large uploads use multipart resumable chunks negotiated via `POST /api/uploads/start` and `PATCH /api/uploads/{id}`.
  - **Camera** captures images/video blobs saved under `object_store/media/captured/`.
  - **From library** browses existing assets through `/api/media/list?folder=...`.
- Once attached, chips display filename, size, and status (`uploading`, `processed`, `error`). Clicking a chip opens the preview drawer.
- The composer renders a thumbnail strip when multiple files are attached; reorder via drag-and-drop, remove via the close icon.

## Role & workflow selector
- Dropdown lists available roles with their default workflow (chat, research, coding, etc.). Data originates from `/api/roles`.
- Selecting a role updates the workflow context and default model binding (see **Model assignment UI** below).
- Adjacent toggle switches between **API** and **Local** execution for the selected workflow. The toggle reads from `GET /api/workflows/{id}/status`, which reports availability of API keys, local models, and remote Float server connections.

## Send actions & streaming
- Pressing send calls `POST /api/chat/messages` with `{ conversation_id, role, workflow_id, message, attachments[], mode: api|local }`.
- Response streaming happens over `/api/stream/thoughts` for thoughts/actions and `/api/stream/responses` (planned) for message chunks. Until `/api/stream/responses` ships, the UI falls back to polling `/api/chat/{conversation_id}/pending`.
- When API or local execution fails, a retry chip appears with the error message surfaced from the backend.

## Keyboard shortcuts
- `Cmd/Ctrl + Enter` – force send even while Shift is held.
- `Esc` – collapse composer (stores draft).
- `Cmd/Ctrl + L` – toggle Live mode.
- `Cmd/Ctrl + U` – open attachment picker.

## Accessibility notes
- All buttons have aria-labels describing their action and current state (e.g., “Toggle live mode, active”).
- Voice capture displays real-time transcript subtitles for screen reader parity.
- Attachment list is keyboard navigable using arrow keys + space/enter.

## API surface summary
| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/chat/messages` | `POST` | Submit a composed message with optional attachments and mode selection. |
| `/api/chat/{conversation_id}/pending` | `GET` | Retrieve queued responses when SSE/WebSocket streaming is unavailable. |
| `/api/chat/voice-stream` | `POST` (chunked) | Accepts streaming audio blobs for push-to-talk capture; returns transcript + attachment IDs. |
| `/api/live/session` | `POST` | Negotiate a live session (Pipecat/LiveKit). |
| `/live/ws` | `WS` | Bi-directional audio stream for live mode. |
| `/api/uploads/start` | `POST` | Initiate a resumable upload; returns upload ID and chunk size. |
| `/api/uploads/{id}` | `PATCH` | Append upload chunks; `status=complete` finalises. |
| `/api/media` | `POST` | Register completed uploads/captures and emit metadata. |
| `/api/media/list` | `GET` | Paginate user media for the attachment library. |
| `/api/roles` | `GET` | List available roles with prompts/tools. |
| `/api/workflows/{id}/status` | `GET` | Report API/local availability, required models, and Float-server connectivity for the selected workflow. |
| `/api/tools/catalog` | `GET` | Provide slash-command tool suggestions. |

This document should be kept in sync with the chat frontend implementation and any new backend routes powering messaging, live capture, or attachments.
