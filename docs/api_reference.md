# Float API Reference

Updated: 2026-04-13

This is a curated reference for the current local FastAPI surface. Most routes are mounted under `/api`; root health probes also exist at `/` and `/health`.

The implementation source of truth is `backend/app/routes.py` plus the app setup in `backend/app/main.py`. Keep this file high-level enough to stay readable, and check route definitions before adding exact request/response schemas.

## Health And Status

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/health`, `/api/health` | `GET` | Basic backend readiness. |
| `/api/mcp/status` | `GET` | MCP bridge status. |
| `/api/celery/status` | `GET` | Worker/broker status summary. |
| `/api/celery/tasks` | `GET` | Current Celery task view for diagnostics. |
| `/api/rag/status` | `GET` | Vector-store and embedding-runtime status. |

## Chat And Continuation

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/chat` | `POST` | Main chat entrypoint. Accepts mode, model/runtime hints, prompt text, attachments, and workflow context. |
| `/api/chat/continue` | `POST` | Continue after tool decisions/results or regenerate from saved assistant state. |
| `/api/llm/generate` | `POST` | Lower-level generation endpoint used by tests and internal callers. |
| `/api/stream/thoughts` | `GET` | SSE stream for thought/tool/agent-console events. |
| `/api/history` | `POST` | Store one timeline/history payload. |
| `/api/history/{session_id}` | `GET` | Read a stored timeline/history payload. |

Chat modes are `api`, `local`, and `server`.

- `api` uses the configured OpenAI-compatible API URL, defaulting to OpenAI Responses.
- `local` uses direct local Transformers or a managed local provider marker such as LM Studio/Ollama.
- `server` uses a user-supplied OpenAI-compatible `server_url` and does not manage that server process.

## Voice And Live Streaming

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/voice/connect` | `POST` | Current cloud-default live voice bootstrap. In Realtime API mode, returns browser-facing Realtime connection details. |
| `/api/voice/stream` | `POST` | Older worker-backed voice stream path; not used when the browser streams directly to OpenAI Realtime. |
| `/api/voice/tts` | `POST` | Text-to-speech generation. Accepts `text`, optional `model`, optional TTS `voice`, and `audio_format` (`mp3`, `opus`, `aac`, `flac`, `wav`, `wave`, or `pcm`). Local TTS currently returns `wav` only. |
| `/api/stream/sessions` | `POST` | Experimental device streaming/session sketch. |
| `/api/stream/candidates` | `POST` | Experimental ICE/signaling candidate route. |
| `/api/stream/sessions/{session_id}` | `GET` / `DELETE` | Experimental streaming session status/end routes. |

OpenAI Realtime is the current cloud-default live voice path. Gemma 4 is not a supported live voice transport in this pass; use Gemma 4 through the language-model lanes. LiveKit remains a fallback transport and Pipecat remains an explored pipeline option.

OpenAI TTS uses `/v1/audio/speech` with `gpt-4o-mini-tts`, `tts-1`, or `tts-1-hd`. TTS voice presets are separate from live Realtime voices and local provider models.

## Tools, Actions, And Agent Console

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/tools/` | `GET` | List registered tools. |
| `/api/tools/specs` | `GET` | JSON-schema tool specs for UI forms and model/tool guidance. |
| `/api/tools/register` | `POST` | Register a tool with the runtime manager. |
| `/api/tools/invoke` | `POST` | Invoke a registered tool. |
| `/api/tools/propose` | `POST` | Store a proposed tool call for user decision. |
| `/api/tools/decision` | `POST` | Approve, deny, or edit a proposed tool call. |
| `/api/tools/client-resolve` | `POST` | Resolve client-side tool work such as camera capture. |
| `/api/tools/schedule` | `POST` | Schedule a tool/action follow-up. |
| `/api/actions` | `GET` | List tracked write actions. |
| `/api/actions/{action_id}` | `GET` | Inspect a tracked action/diff. |
| `/api/actions/revert` | `POST` | Revert tracked write actions when conflict checks allow it. |
| `/api/agents/console` | `GET` | Hydrate Agent Console cards after refresh/reconnect. |
| `/api/background/autonomy/status` | `GET` | Inspect the bounded background autonomy supervisor, attention-ranked reflection candidates, heartbeat, runtime budget, satisfaction threshold, and scheduled-action counts. |
| `/api/background/autonomy/tick` | `POST` | Run or dry-run one bounded background autonomy tick. Payloads can override `mode`, `max_reflections`, `max_runtime_seconds`, and `satisfied_threshold`; routine loop startup remains opt-in via Settings/environment config. |

Current built-in tool metadata lives in `backend/app/tool_catalog.py`; current callable registration lives in `backend/app/tools/__init__.py`.

## Computer Use And Captures

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/computer/capabilities` | `GET` | Report available browser/desktop computer-use runtimes. |
| `/api/computer/sessions` | `POST` | Start a browser or Windows desktop session. |
| `/api/computer/sessions/{session_id}` | `GET` / `DELETE` | Inspect or stop a computer-use session. |
| `/api/computer/screenshots/{filename}` | `GET` | Serve a captured computer-use screenshot. |
| `/api/captures` | `GET` | List transient captures from camera/computer/screen sources. |
| `/api/captures/upload` | `POST` | Upload a capture into transient capture storage. |
| `/api/captures/{capture_id}` | `GET` / `DELETE` | Inspect or delete a capture. |
| `/api/captures/{capture_id}/content` | `GET` | Serve capture content. |
| `/api/captures/{capture_id}/promote` | `POST` | Promote a transient capture into durable attachment/knowledge storage. |

## Memory, Knowledge, RAG, And Attachments

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/memory` | `GET` | List memory entries, optionally detailed. |
| `/api/memory/{key}` | `GET` / `POST` / `DELETE` | Read, upsert, or delete one memory. |
| `/api/memory/{key}/rename` | `POST` | Rename a memory key. |
| `/api/memory/{key}/memorize` | `POST` | Include a memory in retrieval. |
| `/api/memory/{key}/exclude` | `POST` | Exclude a memory from default retrieval. |
| `/api/memory/{key}/archive` | `POST` | Archive/unarchive a memory. |
| `/api/memory/search` | `POST` | Deterministic memory text search. |
| `/api/memory/rag/rehydrate` | `POST` | Reindex memory rows into RAG. |
| `/api/memory/graph` | `GET` | Current memory/provenance graph projection. |
| `/api/knowledge/upload` | `POST` | Upload a document into knowledge. |
| `/api/knowledge/add` | `POST` | Add an existing allowed path or URL-like source. |
| `/api/knowledge/text` | `POST` | Add freeform text. |
| `/api/knowledge/ingest-folder` | `POST` | Ingest a folder under allowed workspace roots. |
| `/api/knowledge/query` | `GET` | Query text/CLIP/hybrid knowledge indexes. |
| `/api/knowledge/list` | `GET` | List indexed knowledge rows. |
| `/api/knowledge/{doc_id}` | `GET` / `PUT` / `DELETE` | Inspect, update, or delete a knowledge row. |
| `/api/knowledge/trace/{doc_id}` | `GET` | Fetch full normalized text/metadata for auditing a retrieved match. |
| `/api/knowledge/file/{doc_id}` | `GET` | Serve a local file only when it resolves inside the managed data/files area. |
| `/api/knowledge/reveal/{doc_id}` | `GET` | Reveal/open a safe local knowledge source location. |
| `/api/attachments/upload` | `POST` | Upload an attachment. |
| `/api/attachments` | `GET` | List attachments. |
| `/api/attachments/rag/rehydrate` | `POST` | Caption/reindex image attachments. |
| `/api/attachments/caption/{content_hash}` | `GET` / `PUT` / `DELETE` | Read, set, or clear an attachment caption. |
| `/api/attachments/reveal/{content_hash}` | `GET` | Reveal/open a safe local attachment location. |
| `/api/attachments/{content_hash}/{filename}` | `GET` | Serve attachment content. |
| `/api/attachments/{content_hash}` | `DELETE` | Delete an attachment. |

SQLite is the canonical knowledge/memory store. Chroma is the default retrieval mirror. Weaviate is optional and exposed through `/api/weaviate/status`, `/api/weaviate/start`, and `/api/knowledge/import/weaviate`.

## Conversations And Threads

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/conversations` | `GET` | List saved conversations; `detailed=true` returns richer metadata. |
| `/api/conversations/{name:path}` | `GET` / `POST` / `DELETE` | Read, save, or delete a conversation by nested path. |
| `/api/conversations/{name:path}/rename` | `POST` | Rename/move a conversation. |
| `/api/conversations/{name:path}/export` | `GET` | Export one conversation. |
| `/api/conversations/export-all` | `GET` | Export all conversations. |
| `/api/conversations/import/preview` | `POST` | Preview import payloads. |
| `/api/conversations/import` | `POST` | Import Markdown/JSON/text/OpenAI-style export content. |
| `/api/conversations/reveal/{name:path}` | `GET` | Reveal a saved conversation location. |
| `/api/conversations/{name:path}/suggest-name` | `GET` | Suggest a better display name. |
| `/api/threads/generate` | `POST` | Generate/update semantic thread summaries. Supports high-level topic inference, seeded `manual_threads`, `embedding_model`, selectable `topic_suggestion_provider`/`topic_suggestion_model`, and default-on `sensitive_mode` that blocks API topic labeling for protected/secret conversation scopes. |
| `/api/threads/summary` | `GET` | Read latest thread summary. |
| `/api/threads/search` | `POST` | Search generated thread data. |
| `/api/threads/rename` | `POST` | Rename a generated thread. |

Conversation sidecar metadata can exist without a matching conversation JSON file. UI/API counts should distinguish real conversation JSON files from metadata-only sidecars.

## Calendar, Tasks, Push, And Notifications

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/calendar/events` | `GET` | List calendar events/tasks. |
| `/api/calendar/events/{event_id}` | `GET` / `POST` / `DELETE` | Read, update, or delete one event/task. |
| `/api/calendar/events/{event_id}/prompt` | `POST` | Prompt/review one event. |
| `/api/calendar/events/{event_id}/run` | `POST` | Manually run an event action. |
| `/api/calendar/import/google` | `POST` | Import Google Calendar payloads. |
| `/api/calendar/import/ics` | `POST` | Import ICS calendar payloads. |
| `/api/calendar/reminders/flush` | `POST` | Flush due reminder prompts after launch/reconnect. |
| `/api/calendar/rag/rehydrate` | `POST` | Reindex calendar events into RAG. |
| `/api/tasks/` | `POST` | Create a task through the shared task surface. |
| `/api/tasks/{task_id}` | `GET` | Read task state. |
| `/api/notify` | `POST` | Add a local notification. |
| `/api/notifications/recent` | `GET` | List recent notifications. |
| `/api/stream/notifications` | `GET` | SSE notifications stream. |
| `/api/push/public-key` | `GET` | Web-push public key. |
| `/api/push/subscribe`, `/api/push/unsubscribe`, `/api/push/test` | `POST` | Web-push subscription management and test. |

## Settings, Themes, Models, And Providers

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/settings` | `GET` / `POST` | Read/update runtime settings and persisted `.env` values. |
| `/api/user-settings` | `GET` / `POST` | Read/update UI/user preferences. |
| `/api/themes` | `GET` / `POST` | List or save user-created themes. |
| `/api/themes/{theme_id}` | `DELETE` | Delete a user theme. |
| `/api/workflows/catalog` | `GET` | Read built-in workflow profile metadata. |
| `/api/openai/models` | `GET` | Cached model inventory for the configured OpenAI-compatible API provider. |
| `/api/llm/provider/status` | `GET` | Managed local provider runtime status. |
| `/api/llm/provider/models` | `GET` | Managed local provider model inventory. |
| `/api/llm/provider/start`, `/api/llm/provider/stop` | `POST` | Start/stop local-managed provider server when supported. |
| `/api/llm/provider/load`, `/api/llm/provider/unload` | `POST` | Load/unload a provider-managed model when supported. |
| `/api/llm/server/models` | `GET` | Probe a Server/LAN OpenAI-compatible endpoint for models. |
| `/api/llm/local-status`, `/api/llm/load-local`, `/api/llm/unload-local` | `GET` / `POST` | Direct local Transformers runtime status/load/unload. |
| `/api/models/supported` | `GET` | Current built-in supported model ids. |
| `/api/models/downloadable` | `GET` | Downloadable model catalog entries. |
| `/api/models/registered` | `GET` / `POST` | Local registered model aliases. |
| `/api/models/registered/{alias}` | `DELETE` | Remove a local registered model alias. |
| `/api/models/jobs` | `GET` / `POST` | List/create model download jobs. |
| `/api/models/jobs/{job_id}` | `GET` | Read one model job. |
| `/api/models/jobs/{job_id}/pause`, `/resume`, `/cancel` | `POST` | Control a model job. |
| `/api/models/info/{model_name}` | `GET` | Model metadata. |
| `/api/models/summary/{model_name}` | `GET` | Short model summary. |
| `/api/models/verify/{model_name}` | `GET` | Verify installed local model files. |
| `/api/models/reveal/{model_name}` | `GET` | Reveal local model path. |
| `/api/models/{model_name}` | `DELETE` | Delete a local model payload. |

Current API defaults focus on `gpt-5.4`; direct-local Gemma 4 targets `gemma-4-E2B-it`; larger Gemma 4 checkpoints are provider/server-first.

## Trusted Devices And Sync

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/devices/register` | `POST` | Register a trusted device. |
| `/api/devices/token` | `POST` | Issue a scoped device token. |
| `/api/devices` | `GET` | List device records. |
| `/api/devices/{device_id}` | `PATCH` / `DELETE` | Update or revoke a device. |
| `/api/devices/prune-legacy` | `POST` | Prune legacy device records. |
| `/api/pairing/offers` | `POST` | Create a pairing offer. |
| `/api/pairing/offers/accept` | `POST` | Accept a pairing offer. |
| `/api/sync/overview` | `GET` | Sync visibility, pairings, workspace profiles, and review state. |
| `/api/sync/pair` | `POST` | Pair with another Float instance. |
| `/api/sync/peer/status` | `POST` | Probe paired peer reachability. |
| `/api/sync/pair/update`, `/api/sync/pair/revoke` | `POST` | Update/revoke saved pairings. |
| `/api/sync/manifest` | `POST` | Remote manifest endpoint for authenticated sync. |
| `/api/sync/export` | `POST` | Remote export endpoint for authenticated sync. |
| `/api/sync/ingest` | `POST` | Remote ingest endpoint, reviewable unless auto-accept is enabled. |
| `/api/sync/plan` | `POST` | Local preview of pull/push changes. |
| `/api/sync/apply` | `POST` | Apply selected sync changes. |
| `/api/sync/reviews/{review_id}/approve`, `/reject` | `POST` | Approve or reject inbound push review items. |

Sync covers conversations, memories, knowledge, graph rows, attachments, calendar files, and workspace preferences. It is an alpha trusted-device flow, not a public gateway or background-sync system.
