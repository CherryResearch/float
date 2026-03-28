not a terminal-style console, but a dock for sub agents.

Agent Cards: Each sub-agent appears as a card showing its current thought stream, task description, and status.

Inline Approvals: Buttons allow quick validation, rejection, or escalation of proposed actions when needed.
    Final outputs get spliced back into the main chat.
    Questions to the user for clarification, direction, feedback, etc. are sent in the main chat or once inter-device connectivity is working to e.g. their phone.

Context Switching: Clicking an agent card opens its dedicated sub-chat, preserving continuity while isolating its reasoning.
    these chats can be linked via weaviate graph to the main chat, live in a subfolder titled after the main chat, or simply have a title referencing the main chat. it's possible that harmony formatting streams would enable saving this as one chat that is parsed into several when opened. managing these efficiently will be important to readability while scaling.

Orchestration: The main chat acts as the orchestrator; the management window ensures visibility and oversight across all active threads.
    sending messages and questions to the main chat does not interrupt workers. It can then interrupt and restart, spin a new worker off, or wait until it is done.

Flow Integration: Thoughts (inline reasoning) and tasks (explicit objectives) are surfaced side-by-side for parallel review.
    the main chats thoughts will appear in line in the normal chat window.

The console now ships as `AgentConsole.jsx` (React) and consumes both the live `/api/ws/thoughts` stream and the `/api/agents/console` snapshot to hydrate cards.
    cards fall back gracefully when the websocket is paused; the refresh button replays the snapshot endpoint.
    each card tracks tool proposals, Celery activity, and memory updates routed through `publish_console_event()` so the UI has consistent metadata.
    calendar day view still mounts inside the same right rail and reuses the shared styling.

Future states: 
    future/past ones can be grayed out (past) /have a pale, themed overlay (light-mint, dark-violet, for future scheduled) to get a continuous scroll 
    potential link with threads feature: specified folder or imputed topic threads to filter these sub agent tasks
    needs to be able to show small, two-line cards or richer 4-6 line ones. active expanded by default, inactive collapsed by default.

potential buttons; 
    stop/continue; approve/deny/edit; click on card to open chat and discuss in line. 

Runtime panel contract (current):

- When local model is a provider marker (`local/lmstudio` or `local/ollama`), the Agent Console runtime panel switches to provider controls.
- Panel exposes provider status pill values: `not installed`, `installed`, `server running`, `model loaded`.
- Actions call provider endpoints:
  - `GET /api/llm/provider/status`
  - `GET /api/llm/provider/models`
  - `POST /api/llm/provider/start`
  - `POST /api/llm/provider/stop`
  - `POST /api/llm/provider/load`
  - `POST /api/llm/provider/unload`
  - `GET /api/llm/provider/logs`
- Existing local runtime panel remains for on-device Transformers models.

