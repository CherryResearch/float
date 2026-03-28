# Knowledge Types and Storage

Float now persists canonical knowledge in SQLite alongside the memory table: `knowledge_items` stores the durable root record and `knowledge_chunks` stores chunked snippets that can rebuild the vector mirror. Chroma remains the searchable mirror by default, holding duplicated chunk text plus lightweight metadata for fast retrieval. Stable `knowledge_id` values survive metadata/source edits; `source` remains the durable origin key and `root_source` links mirrored chunks back to the canonical record.

## Core types

| Type (`metadata.kind` / `metadata.type`) | Origin | Notes |
| --- | --- | --- |
| `memory` | `remember` (optional `vectorize=true`) | `{ id, key/title, value/content, namespace?, tags?, sensitivity?, created_at }`. Lightweight facts/preferences. |
| `document` | `/knowledge/upload`, `/knowledge/add`, `/knowledge/text`, `/knowledge/ingest-folder`, attachments | Raw text plus `{ source, content_type, tags?, created_at }`. Files keep filename + MIME; URLs use the path as `source`. |
| `calendar_event` | Calendar imports/edits, prompts/acknowledgements | Calendar schema: `title`, `start_at`, optional `end_at`, `timezone`, `rrule?`, `status`, `notes`. Rendered to a stable sentence for embedding. |
| `task` (flag on `event`) | Agent-console tasks | Same as `event` with `action_required=true`, optional `due_at`, `status`. Lives in the same table for filtering. |
| `image_caption` | `/knowledge/caption-image` | Caption text for an image; keyed by `source=image:<sha256>`. |

Prefer extending metadata on these primitives before introducing a new discriminator.

## Canonical store and vector mirror

- **SQLite** is the source of truth for text knowledge that participates in retrieval. It stores full text, normalized metadata, summary text, stable IDs, and chunk lineage.
- **Chroma / vector backend** stores searchable chunk duplicates plus the minimum metadata needed to retrieve and audit matches. It is a mirror, not the canonical history.

## Vector mirror and verbs

- **Memorize** (UI verb): upsert the canonical SQLite record, refresh chunk rows, then refresh the vector mirror for searchable snippets.
- **Forget** (UI verb): remove the retrieval-facing knowledge record by `source`/document id; the underlying source file/blob may still exist unless explicitly deleted elsewhere.
- Current backend: Chroma under `data/databases/chroma/` (default). Future graph/vector backends should preserve the same keying convention.
- Multimodal note: CLIP image vectors (when enabled) are stored in a dedicated `KnowledgeClip` index to avoid vector-dimension conflicts with the text embedding index.

## Minimal schema (recommended)

Required: `id`, `kind/type`, `text`, `source`, `created_at`.  
Common optional fields: `key/title`, `source`, `tags`, `namespace`, `sensitivity`, `updated_at`, `status`, `start_at`, `end_at`, `review_at`, `deprecated_at`, `action_required`, `due_at`, `timezone`, `rrule`, `vectorized_at`.
RAG control flags: `rag_excluded` (keep stored but omit from default retrieval).

Nested JSON in metadata is tolerated, but keep top-level fields predictable so UI filters and RAG audits stay stable.

## Processing and retrieval

1) Normalize and stamp: ingestion sets `type` (default `document`) and `created_at` if missing.  
2) Serialize: events/tasks render to a deterministic sentence; memories prefix `key:` (and optional `hint:`) to the stored value text so key-based queries can still retrieve; documents use provided text; captions tag `content_type=image/caption`.  
3) Store and vectorize: write the text + metadata into the vector store keyed by `source` (auto-generated if missing for ad-hoc notes).  
4) Retrieve and audit: `/knowledge/list` returns metadatas; chat responses surface retrieved matches with `type/source` in the context panel.
   Items with `rag_excluded=true` are kept in storage but filtered out of default chat retrieval.

## API note

- `GET /api/knowledge/file/{doc_id}` streams the underlying local file for a knowledge row when its resolved path is inside `data/files`.
- This route is intended for in-app preview/open flows and is blocked for non-local or out-of-scope sources.
- `GET /api/memory/graph` returns the current first-order memory relation graph:
  - memory nodes,
  - explicit provenance anchors (conversation/file/tool/namespace),
  - hybrid semantic edges (embedding score + SAE-style sparse proxy),
  - metadata describing the current signal blend and graph limits.

## Visualization layers

- `Threads` and `Memory` are now separate graph layers that can be rendered together in the `Visualizations` tab.
- Shared anchors between layers (currently conversation references) are rendered as cross-graph bridge edges.
- `Levels` are now the shared UI term for graph-depth focus; the current projection supports:
  - thread -> conversation,
  - memory -> provenance anchor.
- The memory graph is the canonical vector/provenance view.
- The future `knowledge graph overlay` remains the agentic/reasoned layer built on top of that base and should stay conceptually separate from the first-order vector index.

Keep this file aligned with `memory.md`, `calendar_tasks_and_subagents.md`, and `RAG.md` whenever the schema evolves.
