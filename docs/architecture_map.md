# Architecture Map

Updated: 2026-04-18

This map summarizes Float's current code layout, runtime lanes, storage model, and request flow. It is intentionally shorter than the route table; use `docs/api_reference.md` for the public endpoint overview and `backend/app/routes.py` for the implementation source of truth.

## Repository Layout

- `backend/`
  - `app/main.py`: FastAPI app setup, middleware, health probes, route mounting, startup wiring.
  - `app/routes.py`: Current large route module for chat, memory, knowledge, attachments, tools, computer use, calendar, sync, settings, providers, model jobs, actions, and diagnostics.
  - `app/base_services.py`: LLM service orchestration, context assembly, API/local/server dispatch, multimodal payload handling, Harmony handling, and direct-local loading helpers.
  - `app/config.py`: Environment/settings defaults, including API model defaults, streaming settings, local/provider modes, RAG settings, and user settings.
  - `app/tools/`: Built-in tool implementations and tool registration.
  - `app/tool_specs.py`: JSON-schema-like tool argument specs used by the UI and model guidance.
  - `app/tool_catalog.py`: Capability metadata for built-in tools, including status, sandbox, limits, and safety hints.
  - `app/local_providers/`: Managed provider adapters for LM Studio, Ollama, and provider-manager behavior.
  - `app/services/`: Business services for sync, RAG, live/voice transport, model jobs, computer runtime, conversations, and related subsystems.
  - `app/utils/`: Shared stores, blob resolution, graph store, time resolution, argument normalization, device visibility, and other helpers.
  - `app/tests/`: Backend regression tests.
- `frontend/`
  - `src/main.jsx`: React app bootstrap and global state hydration.
  - `src/components/Chat.jsx`: Main chat view, composer, inline command suggestions, attachment/camera flows, and streaming/tool UI.
  - `src/components/TopBar.jsx`: Mode/model controls and top-level navigation.
  - `src/components/HistorySidebar.jsx`: Conversation history, folders, import/export, and navigation.
  - `src/components/AgentConsole.jsx`: Tool/thought/task cards and runtime status side rail.
  - `src/components/KnowledgeViewer.jsx`, `DocumentsTab.jsx`, `KnowledgeSyncTab.jsx`, `ThreadsTab.jsx`, `MediaViewer.jsx`: Knowledge, documents, sync, threads, visualizations, and media surfaces.
  - `src/components/Settings.jsx`: Runtime lanes, provider controls, model jobs, voice/live settings, workflows, tools, sync/device settings, themes, and diagnostics.
  - `src/utils/modelUtils.js`: Frontend model/mode resolution helpers used by chat, top bar, settings, and console.
- `docs/`
  - `feature_overviews/`: Plain-language feature descriptions.
  - `function descriptions/`: Implementation-facing feature specifications.
  - `internal/`: Rolling planning, QA, issue tracking, reports, and session logs.
- `data/` (gitignored): Runtime data root.
- `modules/`: Tracked repo-shipped add-on/skill assets.

## Runtime Lanes

Float keeps these concepts separate:

- `Cloud API` / backend mode `api`: OpenAI-compatible API calls through the configured `api_url` and `api_key`. Defaults currently focus on `gpt-5.4`.
- `Local (on-device)` / backend mode `local`: Direct local Transformers checkpoints or managed provider markers.
- Managed local providers: LM Studio/Ollama-style OpenAI-compatible runtimes managed or probed through `backend/app/local_providers/`.
- `Server/LAN` / backend mode `server`: An already-running OpenAI-compatible server configured by `server_url`; Float does not manage that process.
- Live voice: OpenAI Realtime is the current cloud-default through `/api/voice/connect`; LiveKit remains a fallback transport and Pipecat is still an explored pipeline option. Gemma 4 is not a live voice transport in this pass.

Gemma 4 is lane-scoped:

- `gemma-4-E2B-it` is the current direct-local target.
- `gemma-4-E4B-it`, `gemma-4-26B-A4B-it`, and `gemma-4-31B-it` are provider/server-first.
- Raw GGUF weights should run behind LM Studio, Ollama, or another OpenAI-compatible server, not the direct local Transformers path.

## Data And Storage Model

- `data/conversations/`: Saved conversation JSON files plus `.meta.json` sidecars. Metadata-only sidecars can exist without API-visible conversation JSON.
- `data/databases/memory.sqlite3`: Canonical memory and knowledge store.
- `data/databases/chroma/`: Default Chroma vector mirror for retrieval.
- `data/files/uploads/`: User-uploaded files.
- `data/files/screenshots/`: Capture/screenshot artifacts.
- `data/files/downloaded/`: Approved tool downloads.
- `data/files/workspace/`: Managed document/workspace root for ingest and file workflows.
- `data/sync/<peer>/workspace/`: Canonical custody path for imported synced workspace files.
- `data/models/`: Default local model payload/cache target.
- `data/themes/`: User-created themes from Settings.
- `data/workspace/`: General tool-writable scratch/workspace area.

SQLite is the durable source of truth for memory/knowledge text rows and chunks. Chroma mirrors searchable vector snippets. Weaviate remains optional.

## Chat Request Flow

1. The user sends a message from `Chat.jsx`, optionally with attachments, inline command tokens, workflow/mode settings, and selected runtime/model state.
2. Frontend model helpers resolve API/local/server/provider markers into a concrete request payload.
3. `POST /api/chat` validates the request in `backend/app/routes.py`, stores the user turn, resolves attachment metadata, and builds context.
4. RAG retrieval adds bounded context from canonical knowledge/vector mirrors when enabled.
5. `LLMService.generate()` dispatches to the selected lane:
   - API provider for `api`,
   - direct local Transformers or managed local provider for `local`,
   - OpenAI-compatible `server_url` for `server`.
6. Tool calls are proposed/executed through the shared tool system, with Agent Console/thought-stream visibility.
7. `/api/chat/continue` synthesizes follow-up text after tool decisions/results and persists the final assistant response.
8. Conversation JSON and metadata are updated under `data/conversations/`.

Long-chat handling is intentionally split:

- frontend render windowing keeps the DOM bounded,
- backend save protection prevents trimmed client windows from overwriting the full transcript,
- explicit compaction creates a new working conversation from a summary plus recent raw turns.

## Knowledge And Retrieval Flow

1. Ingestion comes from memory tools, uploads, freeform text, calendar events, attachment captions, folder ingest, or sync.
2. Canonical rows are stored in SQLite as knowledge items/chunks.
3. Text chunks are mirrored into Chroma by default.
4. CLIP image vectors use a separate multimodal index when OpenCLIP dependencies are available.
5. Chat receives a compact retrieved-context block and response metadata records surfaced snippets for audit.
6. Full source payloads can be audited through Knowledge trace/file/reveal routes.

Important caption rule from the 2026-04-12 sync consolidation: imported/manual image captions should not be overwritten by delayed background caption jobs, and generated captions should pass quality guards before replacing existing caption text.

## Tool And Computer-Use Flow

- Built-in callable tools are registered from `backend/app/tools/__init__.py`.
- The compact model-facing discovery surface is `help`; `tool_help` remains a compatibility alias and `tool_info` returns one capability record.
- Tool schemas come from `backend/app/tool_specs.py`.
- Capability metadata comes from `backend/app/tool_catalog.py`.
- The Agent Console hydrates from `/api/agents/console` and streams live state from `/api/stream/thoughts`.
- Browser-first computer-use sessions and guarded Windows desktop sessions are exposed through the shared tool/API surface and keep screenshots/captures in managed storage.

## Sync And Workspace Flow

1. The receiving device turns on LAN visibility.
2. A one-time pairing code creates a trusted-device relationship.
3. `/api/sync/overview` exposes current device state, visibility, pairings, workspaces, and review items.
4. `/api/sync/plan` previews pull/push differences by section and item.
5. `/api/sync/apply` applies selected changes.
6. Remote `/api/sync/manifest`, `/api/sync/export`, and `/api/sync/ingest` handle authenticated peer exchange.
7. Imported workspace custody is recorded under source metadata and canonical sync folders so recursive sync loops can be avoided.

The sync stack is an alpha trusted-device flow. It is not yet background sync, a public relay, or a generic cloud gateway.

## Cross References

- `README.md`: release-facing setup and feature summary.
- `docs/api_reference.md`: current curated API surface.
- `docs/data_directory.md`: runtime storage layout.
- `docs/feature_overviews/README.md`: plain-language feature summaries.
- `docs/feature_overviews/models-and-runtime-modes.md`: runtime lanes and provider modes.
- `docs/feature_overviews/conversations-history-and-storage.md`: conversation storage, history, and context continuity.
- `docs/feature_overviews/voice-live-and-passthrough.md`: voice, live-mode, and passthrough status.
- `docs/feature_overviews/device-sync-and-streaming.md`: trusted-device sync behavior.
- `docs/feature_overviews/tools-and-actions.md`: tool inventory and action semantics.
