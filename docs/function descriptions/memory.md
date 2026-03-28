# Memory Management (Overview)

See `knowledge.md` for how stored memories are surfaced in the shared knowledge fabric / RAG index; this doc focuses on the source schema and lifecycle knobs.

Memory spans conversations, persistent knowledge, media artefacts, and semantic overlays. The UI exposes these through History, Media, Knowledge, and Threads panes; this document links each surface to storage layouts and APIs.

The model-facing entrypoints are `remember` and `recall`:
- `remember` updates the exact memory record and keeps the canonical retrieval copy in SQLite in sync.
- `recall` defaults to hybrid lookup: exact key recall first, then canonical SQLite search plus vector snippets when exact lookup misses. Use `mode=memory|canonical|vector|hybrid` to force a path.

## Knowledge surfaces
- **History sidebar** reads from `data/conversations/` (or `data/test_conversations/` in dev mode) via `/api/conversations`; folder operations mirror filesystem moves/renames.
- **Media/documents surfaces** display uploaded or generated assets from the app-managed file/storage paths (`data/files/*` plus attachment-backed records) and expose zoom/caption/file actions through the current viewer flows.
- **Knowledge tab** reads canonical memory/knowledge records through `/api/memory` and `/api/knowledge/*`; graph-style views are separate projections rather than a direct filesystem table view.
- **Threads tab** consumes generated semantic clusters (`threads/`) described in the dedicated threads specification.

## Media viewer behaviour
- Zoom slider has four stops; each stop adjusts card size and metadata density. Dense list emphasises filename, type, created date; detail view adds transcripts, tags, usage history.
- Hovering a thumbnail reveals a metadata tooltip (dimensions, duration, source, linked conversations, favourite state).
- Opening an item spawns a drawer overlay with the media on top and metadata occupying the lower sixth. Metadata includes editable tags, location in object store, related threads, and knowledge graph nodes.
- Favourites toggle stores state in `media/favourites.json` and reflects immediately in the gallery filter chips.
- Gallery/list share the same selection model so multi-select operations (move, tag, delete) remain consistent.

## Knowledge table & graph
- Default table columns: `memory_id`, `source_chat`, `excerpt`, `tags`, `importance`, `evergreen`, `pinned`, `importance_floor`, `created_at`, `last_accessed`, `thread_ids`, `folder_path`, `sensitivity`.
- `pinned`/`importance_floor` columns render as toggles plus numeric inputs so operators can freeze or floor items without leaving the table.
- Inline edit supports changing tags, importance, sensitivity (with confirmation for promotions to protected/secret).
- `Visualizations` now includes a concrete `memory relation projection`:
  - level 1 = memory items,
  - level 2 = explicit provenance anchors inferred from current memory payloads (`conversation`, `source/file`, `tools`, `namespace`-style markers),
  - semantic memory-memory edges are computed from a hybrid of embeddings and an SAE-style sparse proxy.
- The current API surface for that layer is `GET /api/memory/graph`.
- Multiple graph layers can be shown at the same time; the UI separates them into neighboring point clouds and uses a draggable `plane offset` control plus bridge-color cross-links for shared anchors.
- Deeper recursive levels still need persisted graph snapshots/materialization; the current view is the first-order base layer rather than the full agentic knowledge graph.

## Data model per memory item
- `value`: arbitrary JSON payload (core content).
- `importance`: float weighting for ranking and decay (default 1.0).
- `created_at`, `updated_at`, `last_accessed`: unix timestamps.
- `evergreen`: boolean; defaults to true and keeps the item on the normal decay curve.
- `pinned`: boolean; when true, decay never reduces `importance`.
- `importance_floor`: optional float; decay will not push `importance` below this floor.
- `end_time`: optional timestamp; when past, the item becomes historical (kept but deprioritised).
- `archived`: optional boolean to soft-archive with `archived_at`.
- `sensitivity`: enum (`mundane`, `public`, `personal`, `protected`, `secret`). Protected/secret values require explicit consent before external API usage; secret entries are encrypted.

Lifecycle controls at a glance:
- Use `pinned` when the importance must stay fixed (e.g. standing orders or credentials).
- Combine `importance_floor` with evergreen memories to maintain a baseline weight while still allowing decay above the floor.
- Set `evergreen=false` alongside an `end_time` when items should taper faster once they pass their horizon.

## Persistence
- Memory items now persist to `data/databases/memory.sqlite3` (configurable via `FLOAT_MEMORY_FILE` or the `memory_store_path` config entry). Legacy `data/memory.json` is auto-migrated on first load. The backend creates the directory on demand and keeps it out of version control.
- Setting `FLOAT_DEV_MODE=true` switches the default file to `data/databases/test_memory.sqlite3` so test runs do not overwrite real knowledge.

## Decay semantics
- Exponential decay with configurable rate `r (0 < r <= 1)`; historical/archived items decay faster.
- Decay never deletes items—only reduces retrieval weight.
- Pinned items hold their current `importance` (optionally clamped by an `importance_floor`).
- `importance_floor` ensures scores never drop below a chosen minimum while still allowing decay above that line.
- Evergreen items follow the global decay rate; pair with pinning or floors to express longer retention shapes without full freezes.
- Expiry dates trigger archival once reached.

## Sensitivity handling
- Protected entries can be exposed to external APIs only when a call includes `allow_protected=true`.
- Secret entries remain encrypted at rest (Fernet with keys from settings) and are redacted in exports. Provide optional `hint` fields surfaced at protected level.

## HTTP API
| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/memory?detailed=true` | `GET` | List all items with metadata. |
| `/api/memory/{key}` | `GET` | Fetch a single memory. |
| `/api/memory/{key}` | `POST` | Upsert `{ value, importance?, evergreen?, pinned?, importance_floor?, end_time?, archived?, sensitivity?, hint? }`. |
| `/api/memory/{key}` | `DELETE` | Remove the item. |
| `/api/memory/decay` | `POST` | Apply global decay `{ rate }`. |
| `/api/memory/{key}/archive` | `POST` | Toggle soft-archive `{ archived: true|false }`. |
| `/api/memory/export` | `GET` | Download `memory.jsonl` snapshot for offline backup. |
| `/api/memory/graph` | `GET` | Provide the current memory/provenance graph projection for the graph view. |

## Agent-invokable tools
- `remember(key, value, importance?, sensitivity?, hint?, pinned?, importance_floor?)` - upsert a value with optional lifecycle controls (requires signature).
- Legacy alias: `memory.save(text, namespace?, tags?, vectorize?, graph_triples?, privacy?, source?, key?)` auto-generates a key (when missing) and stores the payload before delegating to the memory manager. It remains available for backward compatibility with older prompts but is not advertised in the system prompt; prefer `remember` for all new flows.
- `recall(key?)` - return a value by key. If the key is missing or unknown, returns `suggestions`, `suggestions_detail` (key + snippet + match type), and `recent_keys` so the model can pick a follow-up key (requires signature).
- `decay_memories(rate=0.95)` - trigger decay (requires signature).
- `review(scope?)` - proactively inspect decaying items, calendar entries, and knowledge records to keep data current. Returns a list of suggested actions.

Ensure new knowledge modalities or storage changes are mirrored here and in the object storage specification.

