# Architecture Map

This map summarises the major directories, the updated API surface, and how a chat request flows through the system.

## Directory structure
- `backend/`
  - `app/`
    - `config.py`: Configuration and environment variable management
    - `main.py`: FastAPI entrypoint
    - `routes/`: API route definitions (chat, memory, threads, calendar, settings)
    - `models.py`: Database models (SQLAlchemy)
    - `schemas.py`: Pydantic schemas
    - `services/`: Business logic (LLM integration, memory, RAG, semantic tags)
    - `tasks.py`: Celery task definitions
    - `utils/`: Helper modules (embedding, tokenizer, security)
    - `tests/`: Backend tests
  - `worker.py`: Celery worker entrypoint
  - `pyproject.toml`, `poetry.lock`: Python dependencies (install with `poetry install`)
  - `Dockerfile`: Container build instructions
- `frontend/`: React/Vite application
  - `src/main.jsx`: Application entrypoint and state provider
  - `src/components/Chat.jsx`: Chat view and composer controls
  - `src/components/HistorySidebar.jsx`: Conversation history navigation
  - `src/components/AgentConsole.jsx`: Right-rail agent console (thoughts/tasks)
  - `src/components/CalendarTab.jsx`: Calendar view
  - `src/components/KnowledgeTab.jsx`: Table/graph viewer
  - `src/components/ThreadsPanel.jsx`: Semantic threads UI
  - `src/styles/`: CSS modules
- `docs/`: Architecture, specs, UI mappings
- `blobs/`, `object_store/` (gitignored): Sample storage roots for conversations/media

## API surface overview
### Chat & live input
- `POST /api/chat/messages` – submit composed messages
- `POST /api/chat/voice-stream` – push-to-talk audio chunks
- `POST /api/live/session` + `WS /live/ws` – negotiate and stream live sessions
- `GET /api/chat/{conversation_id}/pending` – poll pending responses

### Attachments & media
- `POST /api/uploads/start`, `PATCH /api/uploads/{id}` – resumable uploads
- `POST /api/media`, `GET /api/media/list` – register and browse media
- `GET /api/media/{id}` – stream/download media asset

### Memory & knowledge
- `GET/POST/DELETE /api/memory/...` – memory management
- `GET /api/knowledge/graph` – hypergraph data for knowledge tab

### Threads
- `POST /api/threads/generate`
- `POST /api/threads/reembed`
- `GET /api/threads/status/{job_id}`
- `PATCH /api/threads/{thread_id}`
- `GET /api/threads/{thread_id}/items`
- `GET/POST/PATCH/DELETE /api/threads/spools`
- `POST /api/threads/search`
- `GET /api/threads/summary`

### Calendar & sub-agents
- `GET/POST/PATCH/DELETE /api/calendar/events`
- `POST /api/calendar/events/{id}/complete`
- `POST /api/calendar/snooze`
- `GET /api/calendar/missed`

### Settings & workflows
- `GET/PUT /api/storage/config` – object store path
- `GET /api/roles` – role definitions
- `GET/PUT /api/workflows/{id}` – workflow model overrides
- `GET /api/workflows/{id}/status` – availability indicator
- `POST /api/workflows/{id}/test` – health check (planned)
- `GET /api/tools/catalog` – slash command catalogue

### Sync & streaming
- `GET /api/stream/thoughts` – SSE for thoughts/actions
- `GET /api/stream/settings` – SSE for long-running settings/storage actions
- `GET /api/stream/responses` – planned SSE for message deltas
- `GET /api/agents/console` – agent console snapshot used on initial load/refresh

## Chat request flow
1. **Composer**: User types in Chat.jsx; attachments upload via the media routes if needed. Metadata about mode (API/local) and workflow is bound to the request body.
2. **Frontend request**: `POST /api/chat/messages` with `{ conversation_id, message, workflow_id, attachments[], mode }`.
3. **Backend routes**: Request handled by `backend/app/routes/chat.py`. Input validated against schemas, attachments resolved to object store paths, and conversation metadata stored.
4. **Service layer**: `LLMService` (or successor) assembles context from memory/threads, applies workflow-defined prompts/models, and dispatches to API or local model as requested. Tool invocations stream through `/api/stream/thoughts`.
5. **Response streaming**: Thoughts arrive immediately; once `/api/stream/responses` is active it will stream message chunks. For now, final message is persisted and returned in the HTTP response.
6. **Frontend update**: Chat UI renders the response, updates history tree, and records attachments/knowledge references.

## Runtime storage integration
- Conversations, uploads, retrieval mirrors, and workspace files live under `data/` (see `docs/data_directory.md`).
- Some attachment/runtime paths still use `blobs/` for compatibility while the wider storage model continues to consolidate.
- Settings allow relocating the storage root; backend updates watchers and background jobs accordingly.

## Cross references
- `README.md` - setup, runtime modes, and release-facing notes
- `docs/api_reference.md` - public endpoint reference
- `docs/data_directory.md` - runtime storage layout
- `docs/feature_overviews/README.md` - plain-language feature summaries
