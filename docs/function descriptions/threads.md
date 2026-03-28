# Threads (Semantic Grouping)

Threads provides a manual, on-demand semantic grouping view over conversations. Users can generate top-level threads, refine by folder, or generate sub-threads from an existing thread group.

Terminology used in UI/docs:
- `Conversation Threads`: single message chains.
- `Forked Threads`: branch chains that split from a conversation thread.
- `Topic Threads`: cross-conversation semantic bundles (current "threads gallery" objects).
- `Weave View`: graph visualization of `Topic Threads`.

## Current UX
- Generation is manual: the user clicks `Generate threads`.
- The default view is a single horizontal gallery with auto-centering on the active thread:
  - unselected cards stay compact (title + `conv/mentions/messages` chips),
  - the selected thread card expands inline to show:
    - thread action buttons (`Rename`, `Refine`),
    - snippets (sorted to the top by date/score),
    - conversation mentions in a compact two-line row (snippet + metadata/related-topic chips),
    - inline conversation context and related-thread blips.
- Top layout is a single collapsible bar:
  - `Generation` and `Properties` summary chips are shown in the top row,
  - a compact summary row (`threads`, `conversations`, `k selected`) is shown when expanded.
- Conversation focus is URL-addressable:
  - `?tab=threads&thread=<label>&conv=<conversation>&msg=<message_index>`.
- Conversation selection behavior:
  - clicking a conversation mention focuses it inline,
  - the inline viewer expands full message context and keeps selected message centered,
  - neighboring messages are visually offset to make the selected slot easier to track.
- Related-thread blips:
  - blips are color-coded by the target thread (not the currently selected thread),
  - selecting a blip switches thread focus while preserving the selected conversation/message context.
- Advanced generation controls now live in a popup (`Generate options`) instead of always-on inline fields.
- Topic inference can be enabled/disabled (`infer topics`).
- Cluster controls:
  - explicit `k` override (`auto`, `4`, `8`, `16`, `32`),
  - auto-mode soft target (`target k`, default `16`),
  - auto-mode cap (`max k`, default `30`).
- Scope controls:
  - `all conversations`,
  - `folder` (conversation path prefix),
  - `thread group` (refine from an existing thread label, i.e. sub-threads).
- Label handling:
  - optional related-label coalescing (`merge related labels`),
  - current coalescing includes meal/event variants into `Meal Party` where applicable.
- Summary visualization:
  - active thread selection now uses a stronger border/emphasis treatment,
  - gallery ordering shifts toward similarity with the active thread when a thread is focused.
- Generate options clarity:
  - `top-K strategy` copy now explicitly explains auto vs fixed K behavior,
  - helper text/tooltips were expanded for K bounds/scope/manual labels,
  - `Suggest topics` can prefill manual labels from current summary topic stats.
- Correlation ranking for related-thread blips is weighted and normalized client-side:
  - `0.60 * local_topic_count_norm + 0.25 * global_thread_item_norm + 0.15 * recency_norm`.

### Weave View (Topic Graph)
- `Weave View` is a popup/modal graph view of `Topic Threads` and is treated as an alternate presentation of the gallery (not a separate data model).
- Node interactions:
  - single-click selects a node and opens a mini popup card (a compact version of the selected gallery card),
  - popup actions include at least `Open Thread` and `Subthreads`,
  - double-click drills into that node's subthread graph.
- Cross-level visibility (fractal preview behavior):
  - in the fully zoomed-out view, calculated subthread graphs are visible as shrunk previews,
  - the currently selected level/depth is rendered at full opacity and full emphasis,
  - levels above and below the selected level remain visible at lower opacity with paler color treatment,
  - when level selection changes, opacity focus moves to the newly selected level.
- Navigation controls:
  - top-corner graph controls include `Back`, `Forward`, and `Up` (file-explorer style), placed next to the graph open/close control.
  - the shared `Visualizations` tab now also uses the term `levels` and places up/down level controls beside each graph toggle so thread and memory projections use the same navigation language.
- Link semantics:
  - edges remain similarity-driven by default,
  - explicit overlap edges are also supported for "topic jumps" inside a conversation even when topics do not co-mingle in the same message,
  - overlap strength is weighted and rendered distinctly from pure semantic similarity (styling/details TBD in implementation).

## Data/Output Shape
- Summary persists to `data/threads/threads_summary.json` (legacy repo-root `summary.json` is read once and migrated automatically).
- Returned/persisted summary includes:
  - `tag_counts`,
  - `cluster_count`,
  - `clusters`,
  - `conversations`,
  - `threads`,
  - `thread_overview`,
  - `metadata.ui_hints`.
- `thread_overview` is the stable UI contract for aggregated display:
  - `schema_version`,
  - `total_threads`,
  - `threads[]` where each row includes:
    - `id`,
    - `label`,
    - `item_count`,
    - `conversation_count`,
    - `message_count`,
    - `palette_index`,
    - `top_examples[]`,
    - `conversation_breakdown[]`.
- `metadata.ui_hints` currently includes:
  - `k_option`,
  - `k_selected`,
  - `preferred_k`,
  - `max_k`,
  - `coalesce_related`,
  - `merged_label_count`,
  - `scope_mode`,
  - `scope_folder`,
  - `scope_thread`,
  - `thread_overview_version`,
  - `thread_count`.

## API Surface (Implemented)
| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/threads/generate` | `POST` | Generate or refine threads. Supports `{ infer_topics?, tags?, openai_key?, k_option?, preferred_k?, max_k?, coalesce_related?, scope_folder?, scope_thread?, manual_threads?, top_n? }`. |
| `/api/threads/summary` | `GET` | Read latest persisted summary. |
| `/api/threads/search` | `POST` | Semantic search over cached nuggets (`{ query, top_k }`). |
| `/api/threads/rename` | `POST` | Rename/merge a thread label (`{ old_name, new_name }`). |

## Backend Behavior
- Service: `backend/app/services/threads_service.py`.
- Embedding/clustering primitives: `backend/app/services/semantic_tags_service.py`.
- Auto-k uses silhouette scoring with a soft preference toward the configured target k and optional max-k bound.
- Scope behavior:
  - `scope_folder`: includes only conversations under that path prefix.
  - `scope_thread`: loads current summary and keeps only message references present in the parent thread.
- Error behavior:
  - invalid/missing `scope_thread` returns a `400` with a human-readable detail message.

## Notes
- This feature is intentionally manual/on-demand to preserve operator control.
- Subthread groundwork is now exposed in a collapsible funnel pane below the main selected-thread area; current UI uses candidate related topics and is intended as the first layer for recursive drill-down.
- In the current `Visualizations` projection, thread depth is still represented as a shallow thread -> conversation layer. Full recursive subthread levels require persisted graph snapshots rather than the current summary-only projection.

## SAE Signal Path (Roadmap)
- Threads semantic signals will support a sparse autoencoder (SAE) path alongside embeddings.
- Near term: ingest offline activation records and inspect per-token sparse features for auditability.
- Later: apply inference-time steering on selected layers/tokens using decoder directions (`h <- h + Σ alpha_i * decoder[i]`).
- Runtime bridge target: quantized local runtimes (LM Studio/llama.cpp) should export compatible activation records until full unquantized `gpt-oss-20b` hooks are available.

