# Float API Reference

Updated: 2026-08-06

This is a curated reference for the current local FastAPI surface. Most routes are mounted under `/api`; root health probes also exist at `/` and `/health`.

The implementation source of truth is the router aggregate in `backend/app/routes.py`, its included domain routers under `backend/app/routers/` plus legacy extractions such as `backend/app/routes_graph.py`, and the app setup in `backend/app/main.py`. Keep this file high-level enough to stay readable, and check route definitions before adding exact request/response schemas.

## Health And Status

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/health`, `/api/health` | `GET` | Basic backend readiness. |
| `/api/instance` | `GET` | Current software/build receipt and deployment/data identity as separate status dimensions, including deterministic data revision, local revision-observation time, workspace lineage/origin/upstream identity, and latest sync checkpoint. Opening Sync also refreshes this deployment in the machine-local registry. |
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
- `server` uses a preset or user-supplied OpenAI-compatible `server_url` and does not manage that server process. Presets keep bearer tokens in named process environment variables. Tinker inventory is account-aware and includes supported base models plus the account's sampler checkpoints.

The chat, continuation, and generation request models accept `thinking` as a boolean, named level, or numeric effort. Named levels are `none`, `minimal`, `low`, `medium`, `high`, and `xhigh`; numeric efforts are clamped to `0` through `0.99`. Tinker / Inkling receives numeric effort directly, while other recognized reasoning models round it to the nearest named level. These requests also accept optional `max_output_tokens` from `1` through `2000000`. Omitting it leaves the cap to the provider; it is not inferred from reasoning effort. Float returns `metadata.reasoning` for effort details and `metadata.generation` with the explicit maximum or `provider_default`. A provider finish reason such as `length` is reported as `output_truncated` with termination category `output_token_limit`; unsupported reasoning controls, context-window failures, and output-token failures receive separate error categories and hints. Live voice keeps its own provider/session output behavior.

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
| `/api/actions` | `GET` | List tracked write actions plus non-revertible deployment metadata events. Content-bearing undo snapshots still follow the configured Action History retention. |
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
| `/api/captures/upload` | `POST` | Upload a non-empty PNG/JPEG/GIF/WebP capture into transient storage. Declared MIME must match the raster signature; SVG and disguised active content are rejected. |
| `/api/captures/{capture_id}` | `GET` / `DELETE` | Inspect or delete a capture. |
| `/api/captures/{capture_id}/content` | `GET` | Serve a capture with `nosniff`; only verified safe raster content is inline, while legacy/unknown/mismatched content is sandboxed and downloaded. |
| `/api/captures/{capture_id}/promote` | `POST` | Promote a transient capture into durable attachment storage and queue the same caption/text-RAG/CLIP lifecycle as an attachment upload. Fresh jobs are deduplicated and stalled legacy promotions remain retryable. The model-facing `capture.promote` tool uses this same path. |

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
| `/api/graph/schema` | `GET` | Durable graph node/claim schema for tools and editors. |
| `/api/graph` | `GET` / `POST` | Read or upsert durable graph nodes and multi-role claims; `POST` returns an action-history revision when tracking is enabled. |
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
| `/api/attachments/upload` | `POST` | Upload an attachment. The optional `source_url` form field records passive, potentially stale HTTP(S) provenance; Float does not fetch it and rejects URLs carrying userinfo, signed-query, or fragment credentials. |
| `/api/attachments` | `GET` | List attachment descriptors, including the durable content hash, current managed relative path, reconstructable Float retrieval URL, and optional recorded source URL. |
| `/api/attachments/{content_hash}/metadata` | `GET` / `PATCH` | Read or update mutable display name, logical gallery folder, and passive source URL metadata. Hash, physical filename/path, and origin are immutable; API hashes are exactly 64 lowercase hexadecimal characters. |
| `/api/attachments/rag/rehydrate` | `POST` | Caption/reindex image attachments, optionally restricted by content hash. Results distinguish processed images, generated versus unavailable captions, and failures. With the saved cloud lane this operation can send eligible image bytes to the configured provider; the gallery asks again before a bulk run. |
| `/api/attachments/caption/status` | `GET` | Report the saved caption engine plus local installation, dependency, loaded/verified, and first-attempt capability state without downloading a model, loading large weights merely for status, or contacting the provider. |
| `/api/attachments/caption/{content_hash}/generate` | `POST` | Retry one missing/placeholder caption. Manual captions are protected; generated captions require explicit replacement. |
| `/api/attachments/caption/{content_hash}` | `GET` / `PUT` / `DELETE` | Read, set, or clear an attachment caption. Clearing keeps the file and rebuilds local image retrieval without calling a caption provider. |
| `/api/attachments/reveal/{content_hash}` | `GET` | Reveal/open a safe local attachment location. |
| `/api/attachments/{content_hash}/{filename}` | `GET` | Serve attachment content with `nosniff`; only the explicit safe media allowlist is inline, while active/unknown types are sandboxed downloads. |
| `/api/attachments/{content_hash}` | `DELETE` | Delete an attachment plus canonical, text-RAG, and CLIP retrieval records, including namespaced sync mirrors. Partial mirror cleanup leaves a visible retryable tombstone. Deletion invalidates queued index generations so delayed work cannot resurrect metadata or retrieval records. |

SQLite is the canonical knowledge/memory store. Chroma is the default retrieval mirror. Weaviate is optional and exposed through `/api/weaviate/status`, `/api/weaviate/start`, and `/api/knowledge/import/weaviate`.

## Conversations And Threads

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/conversations` | `GET` | List saved conversations; `detailed=true` returns richer metadata. |
| `/api/conversations/{name:path}` | `GET` / `POST` / `DELETE` | Read, save, or delete a conversation by nested path. |
| `/api/conversations/{name:path}/rename` | `POST` | Rename/move a conversation. |
| `/api/conversations/{name:path}/export` | `GET` | Export one conversation. |
| `/api/conversations/export-all` | `GET` | Export all conversations. |
| `/api/conversations/import/preview` | `POST` | Preview JSON/ZIP candidates or classify Markdown/text as a conversation, document, or ambiguous content without writing it. |
| `/api/conversations/import` | `POST` | Import conversation content. Document-classified Markdown/text is rejected; ambiguous Markdown/text requires explicit conversation intent and confirmation. |
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
| `/api/calendar/events/{event_id}` | `GET` / `POST` / `DELETE` | Read, update, or delete one event/task. Delete preserves Activity receipts and returns `409` while a run is active. |
| `/api/calendar/occurrences` | `GET` | Expand stored events over a bounded viewport using the scheduled runner's timezone-aware recurrence rules. |
| `/api/calendar/runs` | `GET` | List the Calendar subset of the durable device-local Activity ledger. Supports event/status filters plus `limit`/`offset`, and receipts remain after their source event is deleted. |
| `/api/calendar/events/{event_id}/prompt` | `POST` | Prompt/review one event. |
| `/api/calendar/events/{event_id}/run` | `POST` | Manually run an event action. |
| `/api/calendar/events/{event_id}/actions/{action_id}/authorization` | `POST` | Approve once or deny one exact occurrence-bound scheduled action request. Decisions are local, digest-bound, and invalidated by relevant edits. |
| `/api/calendar/events/{event_id}/actions/{action_id}/cancel` | `POST` | Request cooperative cancellation for one exact run. A request made after uncertain dispatch preserves reconciliation state instead of claiming rollback. |
| `/api/calendar/import/google` | `POST` | Import Google Calendar payloads. |
| `/api/calendar/import/ics` | `POST` | Import ICS calendar payloads. |
| `/api/calendar/reminders/flush` | `POST` | Flush due reminder prompts after launch/reconnect. |
| `/api/calendar/rag/rehydrate` | `POST` | Reindex calendar events into RAG. |
| `/api/work/runs` | `GET` | List durable Calendar and reflection Activity receipts. Supports source/job filters and `limit`/`offset`; the top-level `count` is the current filtered receipt count and is not promised to share one SQLite snapshot with the returned page. Each receipt's `event_count` counts lifecycle rows, while `attempt_count` and `effect_count` count indexed child snapshots. Retained legacy rows are backfilled idempotently. |
| `/api/work/runs/{receipt_id}/events` | `GET` | Lazily inspect one receipt's append-only status/phase/recovery transitions. `count` is the current lifecycle-row count and paging uses `limit`/`offset`, `has_more`, and `next_offset`. Rows are metadata-only and exclude prompts, summaries, arguments, and raw results. |
| `/api/work/runs/{receipt_id}/attempts` | `GET` | Lazily inspect current indexed provider-attempt snapshots for one receipt, including retry/error categories, retry links, checkpoint/effect-watermark digests, state-delta certainty, and each attempt's internal `transition_count`. Response `count` counts attempt snapshots, not their internal transitions. Prompt bodies, raw provider responses, raw error messages, and checkpoint contents are excluded. |
| `/api/work/runs/{receipt_id}/effects` | `GET` | Lazily inspect each effect's current redacted snapshot and internal `transition_count`, including `intent`/`dispatched`/`acknowledged`/`confirmed`/`unknown`/`not_dispatched` status, scope, replay policy, certainty, permission/approval metadata, digests, and remote operation ids when available. Response `count` counts effect snapshots, not internal transitions. Tool arguments and raw results are excluded; append-only transition rows remain internal. `acknowledged` with `reported_success` records a non-error tool return; `confirmed` is reserved for independent remote-state reconciliation. |
| `/api/work/runs/{receipt_id}/effects/{effect_id}/reconcile` | `POST` | Record `confirm_applied` or `confirm_no_change` for one uncertain effect without replaying it, then project Activity and Calendar state. This is a user-directed receipt, not independent remote verification. |
| `/api/tasks/` | `POST` | Create a task through the shared task surface. |
| `/api/tasks/{task_id}` | `GET` | Read task state. |
| `/api/notify` | `POST` | Add a local notification. |
| `/api/notifications/recent` | `GET` | List recent notifications. |
| `/api/stream/notifications` | `GET` | SSE notifications stream. |
| `/api/push/public-key` | `GET` | Web-push public key. |
| `/api/push/subscribe`, `/api/push/unsubscribe`, `/api/push/test` | `POST` | Web-push subscription management and test. |

Provider-attempt and effect snapshots currently originate from the scheduled-action runner only. Other chat, reflection, delegated-agent, and worker paths can have receipts without these child records.

## Settings, Themes, Models, And Providers

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/settings` | `GET` / `POST` | Read/update runtime settings and persisted `.env` values. |
| `/api/user-settings` | `GET` / `POST` | Read/update UI/user preferences. |
| `/api/themes` | `GET` / `POST` | List or save user-created themes. |
| `/api/themes/{theme_id}` | `DELETE` | Delete a user theme. |
| `/api/workflows/catalog` | `GET` | Read built-in workflow profile metadata. |
| `/api/workflows/skills` | `GET` | List packaged skill documents and local overrides with source, ownership, and linked-module metadata. |
| `/api/workflows/skills/{skill_id}` | `GET` / `PUT` / `DELETE` | Read, save, or remove one local skill override. Create-only saves reject collisions and removing an override restores the packaged fallback when present. |
| `/api/workflows/skills/{skill_id}/duplicate-preview` | `POST` | Prepare an audited create-only duplicate draft without writing a file. |
| `/api/workflows/skills/import-preview` | `POST` | Parse imported Markdown/text as an audited create-only draft without writing it. |
| `/api/workflows/skills/{skill_id}/rename` | `POST` | Rename one unlinked local-only skill document without replacing another id. |
| `/api/workflows/skills/{skill_id}/export` | `GET` | Export the active skill Markdown. |
| `/api/workflows/skills/{skill_id}/draft` | `POST` | Request an audited reflection proposal that remains unsaved until the user explicitly saves it. |
| `/api/openai/models` | `GET` | Cached provider inventory plus `selectable_models`, lifecycle `catalog`, and optional persisted-selection `migration`; accepts `selected_model` and `include_non_chat`. |
| `/api/llm/provider/status` | `GET` | Managed local provider runtime status. |
| `/api/llm/provider/models` | `GET` | Managed local provider model inventory. |
| `/api/llm/provider/start`, `/api/llm/provider/stop` | `POST` | Start/stop local-managed provider server when supported. |
| `/api/llm/provider/load`, `/api/llm/provider/unload` | `POST` | Load/unload a provider-managed model when supported. |
| `/api/llm/server/models` | `GET` | Probe a Server/LAN OpenAI-compatible endpoint for models. Accepts `server_url`, optional `preset_id`, and `refresh`; returns normalized `model_details` when the provider reports context or maximum-output limits. The Tinker preset uses authenticated account inventory rather than assuming a generic `/models` response and currently reports base-model context lengths from the Tinker SDK. |
| `/api/llm/local-status`, `/api/llm/load-local`, `/api/llm/unload-local` | `GET` / `POST` | Direct local Transformers runtime status/load/unload. |
| `/api/models/supported` | `GET` | Current built-in supported model ids. |
| `/api/models/downloadable` | `GET` | Downloadable model catalog entries. |
| `/api/models/registered` | `GET` / `POST` | List user-registered local/Hugging Face models or register an existing local path. |
| `/api/models/registered/huggingface` | `POST` | Normalize and persist a Hugging Face model URL or `owner/repo` in the user's model catalog. |
| `/api/models/registered/{alias}` | `DELETE` | Remove a user-registered local or Hugging Face model alias without deleting external source files. |
| `/api/models/jobs` | `GET` / `POST` | List/create model download jobs. |
| `/api/models/jobs/{job_id}` | `GET` | Read one model job. |
| `/api/models/jobs/{job_id}/pause`, `/resume`, `/cancel` | `POST` | Control a model job. |
| `/api/models/info/{model_name}` | `GET` | Model metadata. |
| `/api/models/summary/{model_name}` | `GET` | Short model summary. |
| `/api/models/verify/{model_name}` | `GET` | Verify installed local model files. |
| `/api/models/reveal/{model_name}` | `GET` | Reveal local model path. |
| `/api/models/{model_name}` | `DELETE` | Delete a local model payload. |

Current API defaults focus on OpenAI `chat-latest` (`GPT latest (...)` in the UI when inventory is available); direct-local Gemma 4 targets `gemma-4-E2B-it`; larger Gemma 4 checkpoints are provider/server-first.

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
| `/api/sync/overview` | `GET` | Sync visibility, pairings, workspace profiles, data revision/checkpoint state, recent deployment metadata events, ledger-chain health, and review state. |
| `/api/sync/lan-visibility` | `POST` | Enable or disable private-network sync and, when Float owns the launcher, restart only the backend on the same port so the saved visibility state matches the real listener. Returns whether the listener is active, restarting, or needs a manual Float restart. |
| `/api/sync/events` | `GET` | List this deployment's content-free software/data event ledger. Supports `limit` and optional `event_type`; returns hash-chain verification state. |
| `/api/sync/pair` | `POST` | Pair with another Float instance. |
| `/api/sync/peer/status` | `POST` | Probe paired peer reachability. |
| `/api/sync/pair/update`, `/api/sync/pair/revoke` | `POST` | Update/revoke saved pairings. |
| `/api/sync/manifest` | `POST` | Remote manifest endpoint for authenticated sync, including deterministic scoped data revision. |
| `/api/sync/export` | `POST` | Remote export endpoint for authenticated sync, including deterministic scoped data revision. |
| `/api/sync/ingest` | `POST` | Remote ingest endpoint, reviewable unless auto-accept is enabled. |
| `/api/sync/plan` | `POST` | Local preview of pull/push changes using a peer/workspace-scoped common ancestor when available. |
| `/api/sync/apply` | `POST` | Apply selected sync changes and record a successful data checkpoint for later creation/edit/deletion/conflict classification. |
| `/api/sync/reviews/{review_id}/approve`, `/reject` | `POST` | Approve or reject inbound push review items. |
| `/api/sync/operations/{operation_id}/cancel` | `POST` | Record cancellation intent for one sync operation; completed remote work is not rolled back implicitly. |

Sync covers conversations, memories, knowledge, graph rows, attachments, calendar files, and workspace preferences. It is an alpha trusted-device flow, not a public gateway or background-sync system.
