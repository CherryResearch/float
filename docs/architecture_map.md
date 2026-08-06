# Architecture Map

Updated: 2026-07-15

This map summarizes Float's current code layout, runtime lanes, storage model, and request flow. It is intentionally shorter than the route table; use `docs/api_reference.md` for the public endpoint overview and the routers included by `backend/app/routes.py` for the implementation source of truth.

## Repository Layout

- `backend/`
  - `app/main.py`: FastAPI app setup, middleware, health probes, route mounting, startup wiring.
  - `app/routes.py`: Compatibility route aggregate for chat, memory, knowledge, attachments, computer use, calendar, sync, settings, model jobs, actions, and diagnostics. New domain routers should be included here while legacy clients/tests still import this aggregate.
  - `app/routers/`: Domain HTTP routers. This name deliberately avoids colliding with the compatibility `app/routes.py` module.
    - `provider.py`: provider status, inventory, lifecycle, logs, and generic OpenAI-compatible server inventory routes. The router is built with the aggregate's shared provider-manager instance.
  - `app/routes_graph.py`: Graph request models plus schema, projection, and update routes; this was the first behavior-preserving extraction and can move under `app/routers/` in a later mechanical cleanup.
  - `app/routes_tools.py`: Tool catalog/specification routes.
  - `app/base_services.py`: LLM service orchestration, context assembly, API/local/server dispatch, multimodal payload handling, Harmony handling, and direct-local loading helpers.
  - `app/config.py`: Environment/settings defaults, including API model defaults, streaming settings, local/provider modes, RAG settings, and user settings.
  - `app/tools/`: Built-in tool implementations and tool registration.
  - `app/tool_specs.py`: JSON-schema-like tool argument specs used by the UI and model guidance.
  - `app/tool_catalog.py`: Capability metadata for built-in tools, including status, sandbox, limits, and safety hints.
  - `app/local_providers/`: Managed provider adapters for LM Studio and Ollama, provider-manager behavior, and pure provider-selection policy in `selection.py`.
  - `app/model_catalog/`: Provider-model lifecycle policy, shutdown metadata, and persisted-selection migration hints.
  - `app/utils/user_model_catalog.py`: User-owned Hugging Face model registrations persisted in `user_settings.json`; these dynamically extend central alias resolution and download-job availability without modifying the shipped built-in registry.
  - `app/routers/`: Extracted domain routers. `model_catalog.py` owns cloud inventory/catalog responses and `provider.py` owns managed/local provider controls while `app/routes.py` remains the compatibility aggregate.
  - `app/services/`: Business services for sync, RAG, live/voice transport, model jobs, computer runtime, conversations, graph payload normalization, provider-agnostic model inventory, and related subsystems.
  - `app/utils/`: Shared stores, blob resolution, graph store, deployment/build status, time resolution, argument normalization, device visibility, and other helpers.
  - `app/tests/`: Backend regression tests.
- `frontend/`
  - `src/main.jsx`: React app bootstrap and global state hydration.
  - `src/components/Chat.jsx`: Main chat view, composer, inline command suggestions, attachment/camera flows, and streaming/tool UI.
  - `src/components/TopBar.jsx`: Mode/model controls and top-level navigation.
  - `src/components/HistorySidebar.jsx`: Conversation history, folders, import/export, and navigation.
  - `src/components/AgentConsole.jsx`: Tool/thought/task cards and runtime status side rail.
  - `src/components/KnowledgeViewer.jsx`, `KnowledgeSkillsTab.jsx`, `DocumentsTab.jsx`, `KnowledgeSyncTab.jsx`, `ThreadsTab.jsx`, `MediaViewer.jsx`: Knowledge, skill/workflow management, documents, sync, threads, visualizations, and media surfaces.
  - `src/components/Settings.jsx`: Runtime lanes, provider controls, model jobs, voice/live settings, capture/privacy controls, tools, sync/device settings, themes, and diagnostics.
  - `src/utils/modelUtils.js`: Frontend model/mode resolution helpers used by chat, top bar, settings, and console.
- `docs/`
  - `feature_overviews/`: Plain-language feature descriptions.
  - `function descriptions/`: Implementation-facing feature specifications.
  - `internal/`: Rolling planning, QA, issue tracking, reports, and session logs.
- `data/` (gitignored): Runtime data root.
- `modules/`: Tracked repo-shipped add-on/skill assets.

User model custody stays split by source: local path registrations point at existing files and are never deleted as part of unregistering; Hugging Face registrations store only normalized repository metadata until the user explicitly starts a download job.

## Runtime Lanes

Float keeps these concepts separate:

- The local launcher binds the backend to `127.0.0.1` until the user explicitly enables `Visible on LAN` (or passes `--lan`). A normal launcher-managed UI change restarts only the backend on the same port and re-reads the saved preference; explicit host overrides and `--dev` source-auto-reload sessions stay locked and require a full restart. `--lan`, `--no-lan`, and `--backend-host` remain explicit per-run overrides. The development frontend proxy uses IPv4 loopback so it remains connected after an IPv4 `0.0.0.0` bind. Ordinary remote browser traffic should still enter through Float's same-host frontend proxy, while paired-device endpoints retain LAN visibility and bearer-scope checks.

- `Cloud API` / backend mode `api`: OpenAI-compatible API calls through the configured `api_url` and `api_key`. The only static frontend default is `chat-latest`; live provider inventory supplies the concrete choices. The backend catalog hides officially deprecated/removed ids from new selection while retaining a persisted old choice long enough to show its lifecycle and migration target.
- `Local (on-device)` / backend mode `local`: Direct local Transformers checkpoints or managed provider markers.
- Managed local providers: LM Studio/Ollama-style OpenAI-compatible runtimes managed or probed through `backend/app/local_providers/`.
- `Server/LAN` / backend mode `server`: An already-running OpenAI-compatible server configured by `server_url`; Float does not manage that process. Built-in and user presets keep endpoint/auth-environment metadata in `server_presets.py`, while the official Tinker SDK supplies account base-model and sampler-checkpoint inventory. Inference still uses the shared OpenAI-compatible chat transport.
- Live voice: OpenAI Realtime is the current cloud-default through `/api/voice/connect`; LiveKit remains a fallback transport and Pipecat is still an explored pipeline option. Gemma 4 is not a live voice transport in this pass.

Gemma 4 is lane-scoped:

- `gemma-4-E2B-it` is the current direct-local target.
- `gemma-4-E4B-it`, `gemma-4-26B-A4B-it`, and `gemma-4-31B-it` are provider/server-first.
- Raw GGUF weights should run behind LM Studio, Ollama, or another OpenAI-compatible server, not the direct local Transformers path.

## Data And Storage Model

- `data/conversations/`: Saved conversation JSON files plus `.meta.json` sidecars. Metadata-only sidecars can exist without API-visible conversation JSON.
- `data/databases/memory.sqlite3`: Canonical memory and knowledge store.
- `data/databases/reflections.sqlite3`: Canonical reflection task/run store.
- `data/databases/work_runs.sqlite3`: Device-local Activity ledger with current run snapshots, append-only lifecycle rows, and current indexed provider-attempt/effect snapshots backed by internal transition rows. Receipt `event_count` counts lifecycle rows; `attempt_count` and `effect_count` count indexed child snapshots, while each child exposes its own `transition_count`. Prompt bodies, tool arguments, raw provider/tool results, conversation bodies, thought traces, checkpoint bodies, and dedicated raw error bodies are not allowlisted in child evidence. Bounded caller-derived receipt summaries can still contain useful result or safe error text, so the ledger is local user data even though it is not synced or indexed into Knowledge.
- `data/databases/deployment_events.sqlite3`: Deployment-local, append-only metadata ledger for software installs, syncs, and data-store mutations. It stores UUIDs, revisions, sections, and counts only; it does not store item ids, filenames, prompts, conversation text, memory text, or sync payloads.
- `data/databases/chroma/`: Default Chroma vector mirror for retrieval.
- `data/files/uploads/`: User-uploaded files.
- `data/files/screenshots/`: Capture/screenshot artifacts.
- `data/files/downloaded/`: Approved tool downloads.
- `data/files/workspace/`: Managed document/workspace root for ingest and file workflows.
- `data/sync/<peer>/workspace/`: Canonical custody path for imported synced workspace files.
- `data/models/`: Default local model payload/cache target.
- `data/themes/`: User-created themes from Settings.
- `data/deployment.json`: Runtime-owned stable UUID for this deployment/data root. It is created lazily and is not shipped in release snapshots.
- `data/workspace/`: General tool-writable scratch/workspace area.

The shipped source root may also contain `.float-build.json`, generated by the release snapshot helper. It keeps the stable release version separate from the occasional human build code and records the source revision, dirty-source flag, deterministic snapshot digest, and build time. A non-Git installation managed by `scripts/deploy_release_snapshot.py` also owns `.float-deployment-manifest.json`, which records the exact installed shipped-file set so later pushes can remove only previously managed stale files while preserving runtime data, settings, logs, models, notebooks, and dependencies. Each successful managed install appends a content-free `software.install` event to that deployment's ledger. The machine-local deployment registry lives outside every repo/data root at `%LOCALAPPDATA%/Float/deployments.json`, so independent deployments can discover one another without sharing their runtime data.

The deployment event ledger is deliberately separate from synced user content and from the short-retention Action History snapshots used for undo. Each deployment keeps its own chain; ledgers are not synced between peers. Every newly observed deterministic full-data revision appends a local timestamped revision event, while memory-store writes additionally record count-only update/delete/bulk-replacement detail. Event hashes chain to the previous event so later metadata edits are detectable, while normal user content can still be deleted, cleaned, or allowed to expire without being copied into an audit archive.

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
- Calendar is canonical for background-work start time, time zone, recurrence, and series bounds. `app/services/calendar_jobs.py` supplies the shared timezone-aware occurrence expansion used by both the Calendar UI API and the scheduled-action runner.
- `CalendarEvent.background_job` keeps patience, execution preferences, and ownership/lineage separate from schedule fields. Calendar shows future occurrences, Agent Console shows current-session agents/jobs, and `/api/work/runs` reads durable Calendar/reflection receipts from `work_runs.sqlite3`. The scheduled-action runner writes a receipt before tool/provider work, records provider attempts, journals mutating or unknown-effect tool intent before dispatch, and advances stable ids through retry, effect-certainty, follow-up, and recovery phases. Effects progress from `intent` to `dispatched` and then `acknowledged`, `unknown`, or `not_dispatched`; exceptions or error-shaped results after dispatch remain unknown and are never automatically replayed. `acknowledged` records a successful tool return, while `confirmed` is reserved for future independent remote-state verification. Calendar `run_history` and reflection rows remain compatibility sources for idempotent startup/read backfill, so deleting a Calendar event no longer deletes its Activity history. This journal currently covers the scheduled runner only; protected checkpoint capsules, prompt-only process-loss resume, hard cancellation, permission enforcement, and external reconciliation remain open.
- The bounded autonomy supervisor wraps reflection scheduler ticks with a durable heartbeat, dry-run planning, attention-ranked candidates, and an opt-in loop controlled by `FLOAT_BACKGROUND_AUTONOMY_ENABLED` plus Settings background controls. Legacy `manual`, `basic`, `overnight`, `extended`, and `always_on` values remain accepted, while the UI frames them as termination/budget posture. `FLOAT_BACKGROUND_AUTONOMY_SANDBOX_PROCESSES` records an isolation preference when a sandbox backend is available.
- Live container orchestration/API background-response checks live in `backend/app/tests/integration/test_autonomy_container_orchestration.py` and are skipped unless `FLOAT_RUN_AUTONOMY_INTEGRATION_TESTS=1` is set. Use `scripts/run_autonomy_integration_tests.ps1` to run that suite separately from normal Poetry test passes.
- Browser-first computer-use sessions and guarded Windows desktop sessions are exposed through the shared tool/API surface and keep screenshots/captures in managed storage.

## Sync And Workspace Flow

1. The receiving device turns on LAN visibility.
2. A one-time pairing code creates a trusted-device relationship.
3. `/api/sync/overview` exposes current device state, software and data identity, deterministic data revision, latest per-peer checkpoint, recent content-free deployment events, visibility, pairings, workspaces, and review items. Workspace summaries carry an inherited logical lineage UUID plus origin and immediate-upstream deployment UUIDs. A verified peer check retains the peer's last observed deployment UUID and software/data summary in its pairing record.
4. `/api/sync/plan` previews pull/push differences by section and item. It uses a peer/workspace-scoped common ancestor when available. The first authenticated comparison may anchor a complete observed section without calling it a successful sync; later previews do not advance that anchor, so downstream creations remain identifiable until selected sync applies them.
5. `/api/sync/apply` applies selected changes, records successful selected-item fingerprints, and appends local sync metadata with before/after revisions and counts. A push sends only a causal event UUID to the receiver, so the two local ledgers can be correlated without copying either ledger or user content.
6. Remote `/api/sync/manifest`, `/api/sync/export`, and `/api/sync/ingest` handle authenticated peer exchange.
7. Imported workspace custody is recorded under source metadata and canonical sync folders. Replicas inherit the original workspace lineage/origin, record their immediate upstream deployment, and use that deployment identity in the recursive-sync guard.

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
