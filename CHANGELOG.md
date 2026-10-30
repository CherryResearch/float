# Changelog

## 2026-10-30 alpha patch

- Repaired chat request ownership when switching conversations or models,
  reopening a pending turn, stopping, regenerating, or continuing after tool
  review. Stale regeneration and continuation replies no longer replace newer
  saved answers, and duplicate continuation attempts preserve the request
  already in progress.
- Fixed camera attachment rendering for image routes without file extensions,
  added vision-mode explanations, and scoped image-delivery notices to the
  relevant request. Recall cards display returned matches and suggestions;
  approval cards show the requested arguments and link to review in Agent Console.
- Added provider-reported model identity, available sent, accepted, first-output
  and completion timing, and usage to message details. Supported GPT Responses requests ask for
  provider-exposed reasoning summaries across HTTP and WebSocket transports;
  summary text and reasoning token counts depend on what the provider supplies.
- Improved saved-settings hydration and writes, Documents controls, Calendar
  event identity, Activity response handling, and memory privacy projections.
- Added bounded local-only graph queries with exact relation, role and time
  filters, complete retained attribution, and explicit historical-query limits.
  Graph undo/redo now restores selected records and complete claim roles in a
  SQLite transaction, with conflict checks and rollback on invalid restoration.
- Made raw OpenAI request/response captures opt-in and bounded by size, count
  and lazy age retention, with local random filenames. This policy does not
  cover other application logs or delete previously captured data.
- Added frontend CI checks, collection of all configured backend test roots,
  and clearer root-workspace setup and runtime documentation.

Known follow-ups include saved Agent Console summary replay, shared WebSocket
ownership, some saved error/partial
response recovery cases, and broader retrieval ranking and verbose-query quality.
Provider summary support varies by model and endpoint. Graph restore does not make
mixed-section undo or Action History JSON atomic, or repair incomplete historical
snapshots. Scheduled provider process isolation does not sandbox tool execution.
Reflection and scheduled model generation refuse graph-marked conversations
until their saved outputs and retrieval mirrors can preserve local-only custody;
foreground graph work remains available with a local model.

## 2026-08-06 alpha patch

- Made Calendar-backed background work restart-safe and auditable with durable
  prompt checkpoints, scoped scheduled-tool authorization, cooperative stop
  requests, effect receipts, and no-replay reconciliation controls.
- Completed the local skill-document lifecycle with guarded create, edit,
  duplicate, import, rename, export, delete, and packaged-fallback restore
  flows, plus clearer module ownership and tool access.
- Stabilized chat, visual recall, and attachment handling, including
  captionless CLIP retrieval, clearer tool progress, safer whole-chat undo,
  and better composer and receipt density.
- Improved device Sync and LAN controls with identity-aware address updates,
  safer review/apply behavior, clearer connection state, and protected release
  deployment cleanup.
- Refined Calendar, Activity, Skills, Sync, Settings, and navigation at the
  standard 1920x1080 desktop target with clearer action hierarchy and labels.

## 2026-07-25 alpha patch

- Hardened tool approval and continuation so denied or completed requests stay
  terminal, repeated loops stop cleanly, and newer user turns supersede stale
  background work.
- Added safer deployment and device-sync foundations, including protected
  runtime data, build receipts, review checkpoints, and clearer ownership state.
- Expanded provider and runtime configuration for OpenAI-compatible servers,
  Tinker / Inkling, Anthropic-compatible, Gemini-compatible, and OpenRouter
  connections, with clearer model identity and reasoning/output controls.
- Made mobile and webcam attachments durable across refresh and reconnect, and
  improved Responses API visual follow-ups.
- Added deterministic conversation-versus-document import review for Markdown,
  text, JSON, and OpenAI export archives.
- Reorganized Settings around clearer Connections, Models, Visual data, and
  progressive-disclosure controls.
- Updated frontend dependency security and general backend/frontend reliability.

## 2026-05-02 alpha patch

- Tool discovery cleanup with curated help and recovery hints.
- Agent Console, retrieval, STT, and UI polish.
- Background reflection and conversation compaction surfaced more clearly.
- README/runtime catalog updated.
- General bugfixes.
