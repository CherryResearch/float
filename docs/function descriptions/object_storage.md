# Object Storage & File System Layout

Float keeps a configurable object store that unifies conversations, media, generated artifacts, and derived indexes. This document defines the default structure, how it maps to the UI, and how settings let users relocate or extend the storage tree.

## Base location
- Default root: `object_store/` inside the Float data directory (gitignored).
- Override via **Settings → Storage** using either a local filesystem path or a mounted network share. The backend exposes `GET/PUT /api/storage/config` to read or update the base path.
- When relocating, the backend validates access, migrates directory stubs (unless `skip_seed=true`), and reindexes pointers stored in the database.

## Directory structure
```
object_store/
  conversations/
    <conversation-id>/
      transcript.jsonl
      draft.json
      attachments/
      timeline/
  media/
    uploads/
    captured/
    generated/
    favourites.json
  knowledge/
    memory.jsonl
    tables/
    graph/
      nodes.parquet
      edges.parquet
    embeddings/
      text.faiss
      vision.faiss
  threads/
    summaries/
    spools/
      <spool-id>.json
  cache/
    models/
    tmp_uploads/
  settings/
    roles.json
    workflows.json
```

### Conversations
- Each conversation folder mirrors the history tree visible in the **History** sidebar. Nested folders inside `conversations/` represent user-created folders.
- `transcript.jsonl` stores messages, thoughts, and tool calls. `timeline/` holds computed metadata such as reaction markers, approvals, or bookmarks.
- Attachments referenced in the conversation point to entries under `media/` via relative paths.

### Media
- `uploads/` holds direct file uploads, `captured/` stores camera captures, and `generated/` stores model-produced media.
- `favourites.json` lists starred items and folder references so the UI can filter quickly.
- Metadata for each asset is indexed in the database and cached in `media_index.parquet` (implicit, regenerated nightly).

### Knowledge
- `memory.jsonl` mirrors the `/api/memory` store for quick local backups.
- `tables/` contains named tabular exports (CSV/Parquet) for knowledge collections.
- `graph/` holds hypergraph nodes/edges plus derived views for the graph explorer.
- `embeddings/` stores FAISS (or similar) indexes keyed by modality.

### Threads
- `summaries/` contains the latest `summary.json` per scope (global or folder).
- `spools/` stores saved bundle definitions used by the threads UI. Each JSON file contains `{ name, thread_ids[], filters }`.

### Cache
- `models/` caches downloaded model weights when a per-workflow override is configured.
- `tmp_uploads/` buffers chunked uploads before finalising. Files automatically move to `media/uploads/` when completed.

### Settings
- `roles.json` and `workflows.json` are materialised snapshots of the settings UI. Editing them manually is discouraged; use the `/api/settings/...` routes to ensure validation.

## UI reflection
- **History tree**: reads the directory hierarchy under `conversations/`. Renaming a folder updates both the filesystem and the metadata entry via `PATCH /api/conversations/{id}`.
- **Media gallery**: pulls from `media/` and respects favourite flags + folder assignments stored in metadata.
- **Knowledge tab**: loads tables/graph/embeddings to populate the spreadsheet view and hypergraph explorer.
- **Threads**: consumes `threads/summaries/` and `threads/spools/` for sliders, filters, and saved bundle dropdowns.

## Settings integration
- Storage page shows the current root, free space, and a health indicator (read/write checks, last index time).
- Users can mount additional folders (e.g., `Research`, `Projects`) which appear as symbolic links within `object_store/extra/`. These links show up as top-level folders in the History tree and media browser.
- Admin actions (e.g., move store, verify integrity) emit progress updates over `/api/stream/settings` so the UI can display status toasts.

## Synchronisation & backup
- Sync engine watches `object_store/` for changes and publishes deltas to connected devices. Cursor-based feeds reference file paths relative to the root so remote devices can reconstruct structure.
- Optional periodic snapshotting writes compressed archives to `object_store/backups/` with retention policies configured via settings.

Keep this layout aligned with backend ingestion pipelines and update it when new modalities or storage backends are added.
