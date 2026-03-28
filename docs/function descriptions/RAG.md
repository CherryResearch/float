# Retrieval-Augmented Generation (RAG)

Float's RAG subsystem keeps a lightweight vector index alongside conversational history so agents can pull grounded snippets into prompts. This document outlines the pieces involved, how data flows through the backend, and where the UI surfaces that state.

## Purpose
- Normalize disparate knowledge (memories, uploaded docs, captions) into a shared vector store.
- Expose the indexed corpus in the Knowledge tab so operators can audit what the model sees.
- Inject retrieved snippets into chat requests, and show the exact matches back to the user.

See also `knowledge.md` for category-specific metadata and the unified schema.

## Backend pipeline
1. **Ingestion triggers**
   - `remember` writes the exact value into the durable memory store, keeps a canonical retrieval record in SQLite, and mirrors searchable snippets into the vector index when mirroring is enabled. The embedded text includes the `key` (and `hint` when present) so exact names remain retrievable; metadata fields alone are not embedded. The legacy `memory.save` alias is supported for backward compatibility but should not be surfaced to the model.
   - Knowledge uploads hit `/api/knowledge/upload` (files), `/api/knowledge/add` (paths/URLs), or `/api/knowledge/text` (plain text) which call `RAGService.ingest_*`. The canonical record is stored in SQLite and chunked snippets are mirrored into the vector backend.
   - Image captioning (`/api/knowledge/caption-image`) stores the caption as a text entry (`kind=image_caption`, `content_type=image/caption`). Best-effort: when `open_clip` is installed, Float also stores the image's CLIP embedding into a dedicated CLIP index (`KnowledgeClip`, `kind=image_embedding`) keyed by the same `source=image:<sha256>`. CLIP vectors are not decodable back into text, so keep the caption for auditing and prompt injection.
   - Calendar events (imports, manual edits, prompts) serialize to short summaries and are ingested automatically (typically `kind=calendar_event` with `event_id`, `start_time/end_time/timezone` in metadata). `source` is derived from `event_id` to keep updates stable.
2. **Canonical storage + chunking**
   - `RAGService` now persists canonical roots in SQLite (`knowledge_items`) and chunk rows in `knowledge_chunks`.
   - Chunking is shared with the thread-generation pipeline so RAG and thread summaries split text the same way.
3. **Embedding**
   - `RAGService` wraps the configured embedding backend (`rag_embedding_model` setting). Default is `local:all-MiniLM-L6-v2`.
   - If the local SentenceTransformer weights are unavailable (offline or missing deps), the embedder falls back to deterministic hash embeddings.
   - API-based embeddings (`api:*` identifiers) call the configured `/v1/embeddings` endpoint; failures fall back to the hash embedder.
   - CLIP embeddings (`clip:*`) use OpenCLIP when available; failures fall back to the hash embedder.
4. **Vector store**
   - Backends: Chroma (default, stored under `data/databases/chroma/`), Weaviate client, or an in-memory fallback. The `rag_backend` config controls the selection.
   - Schema decision: vector entries mirror chunk text and retrieval metadata only. Canonical IDs live in SQLite; chunk mirrors carry `knowledge_id`, `root_source`, and chunk ordinals so the vector layer can be rebuilt.
   - `app/services/rag_provider.py` keeps a singleton instance that can be reused by HTTP routes and tools without duplicating setup cost.
5. **Retrieval**
   - `POST /chat` calls `service.query(message, top_k=3)` before sending the user prompt to the LLM.
   - Retrieved matches are injected into the prompt as a transient system message and mirrored into the response metadata so the UI can render them.
     - Chat metadata stores snippets (size-capped) to keep conversation logs small; fetch full text via `/api/knowledge/trace/{doc_id}` when auditing.
   - Retrieval respects `rag_excluded` / `excluded` metadata flags (stored-but-omit) so operators can keep items indexed but prevent them from being pulled into default chat context.
   - The `recall` tool defaults to a bounded hybrid path: exact memory lookup first, then canonical SQLite search plus vector snippets when exact lookup misses.
6. **Maintenance jobs**
   - `/memory/rehydrate` schedules a Celery task that scans the memory store for string entries that have never been vectorized and backfills them with `kind=memory_refresh` metadata (recording `vectorized_at` on success).
   - `/memory/rag/rehydrate` performs the same scan synchronously (non-Celery fallback for local/dev runs).
   - `/calendar/rag/rehydrate` re-indexes stored calendar events into the knowledge base.
   - `/attachments/rag/rehydrate` (re)captions and indexes existing image uploads into the knowledge base (caption text + best-effort CLIP vectors).
   - `/memory/search` performs an on-demand plaintext sweep across the memory store so operators (or Float itself) can deep-dive deterministically when RAG results need augmentation. Use this before/after rehydration jobs to confirm coverage.

## UI touchpoints
- **Settings > Service status** shows the RAG backend state (documents, disk usage, backend type) along with a selector for the embedding model.
- **Knowledge > Documents** lists indexed items, allows uploading files / entering freeform text, and displays the raw metadata to clarify what will be retrieved.
- **Chat** renders a "Retrieved context" accordion per assistant reply with the source label, score, document ID, and snippet for each match; sources deep-link back into the Knowledge browser (memories/calendar/documents).
- **Knowledge > Documents** also supports semantic querying via `/api/knowledge/query` with `mode=text|clip|hybrid` (hybrid prefers CLIP image matches then fills with text matches).

## Configuration knobs
| Setting | Location | Description |
| --- | --- | --- |
| `rag_backend` | `.env` / Settings API | `chroma`, `weaviate`, or `auto`. Controls which vector store implementation is used. |
| `chroma_persist_dir` | `.env` | Filesystem path for the local Chroma database. |
| `rag_embedding_model` | Settings UI | Logical identifier for the embedding pipeline. Supports `simple` (hash fallback), `local:<sentence-transformers name>`, and `api:<provider-model>` (stubbed). |
| `rag_clip_model` | `.env` / Settings UI | Optional CLIP model id (OpenCLIP) used for the `KnowledgeClip` multimodal index (defaults to `ViT-B-32`). |
| `rag_chat_top_k` | `.env` / config | Number of text matches retrieved for chat (env `RAG_CHAT_TOP_K`, default `3`). |
| `rag_chat_clip_top_k` | `.env` / config | Number of CLIP matches retrieved for chat (env `RAG_CHAT_CLIP_TOP_K`, default `2`). |
| `rag_chat_match_chars` | `.env` / config | Max characters stored per match in chat metadata/history (env `RAG_CHAT_MATCH_CHARS`, default `1200`). |
| `rag_chat_prompt_snippet_chars` | `.env` / config | Max characters per match in the prompt-injected RAG snippet (env `RAG_CHAT_PROMPT_SNIPPET_CHARS`, default `240`). |
| `rag_chat_prompt_max_chars` | `.env` / config | Max characters for the prompt-injected RAG system message (env `RAG_CHAT_PROMPT_MAX_CHARS`, default `2200`). |
| `/api/knowledge/*` | HTTP | REST surface for listing, adding, updating, and deleting indexed documents. |

## Extending the system
- **Hosted embeddings**: `api:*` embedding identifiers already route through the configured `/v1/embeddings` endpoint. Remaining work is provider presets, clearer failure/reporting UX, and better default selection guidance.
- **Additional inputs**: drop files into a watched folder via `RAGService.watch_folder` to auto-ingest newly-created `.txt` / `.md` artifacts.
- **Auditing**: call `/api/knowledge/trace/{doc_id}` to fetch the normalized `{id, text, metadata}` payload for any match surfaced in the Chat UI (and `/api/knowledge/{doc_id}` for the Chroma-style `{ids, documents, metadatas}` wrapper used by the UI/tools).

Keep this document in sync whenever we add new ingestion paths, swap vector stores, or change how retrieved snippets are displayed.
