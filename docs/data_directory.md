# Data Directory

Float keeps runtime artifacts in `data/` (gitignored) so local installs stay tidy and private. Everything an agent writes should land somewhere inside this tree unless the user explicitly approves a different mount.

## Goals

1. **Single writable root** - permissions stay predictable on Windows/Linux/macOS because tools touch `data/` rather than arbitrary OS paths.
2. **Workspace sandboxing** - personal-device streaming and remote sessions expose managed folders, not the whole host filesystem.
3. **Readable layout** - humans can inspect uploads, screenshots, downloads, workspace roots, or databases without guessing where files live.

## Layout

```text
data/
  conversations/               # chat transcripts (+ *.meta.json sidecars)
  threads/                     # generated thread summaries (threads_summary.json)
  databases/
    memory.sqlite3             # memory store backing /memory endpoints
    reflections.sqlite3        # canonical reflection tasks and runs
    work_runs.sqlite3          # durable local Activity receipts + lifecycle states
    deployment_events.sqlite3 # content-free local software/data event ledger
    calendar_events/           # JSON payloads for upcoming/past events
    chroma/                    # Chroma vector store used by the RAG backend
  files/
    uploads/                   # user-provided files routed through the UI/API
    screenshots/               # captures created during streaming sessions
    downloaded/                # assets fetched by approved tools
    workspace/                 # root workspace plus named workspace roots / nested sync copies
  models/                      # default download/cache target for local models
  modules/
    addons/{addon}/config.json # local add-on manifest/config overrides
    skills/{skill_id}.md       # local skill markdown overrides
  themes/                      # user-created visual themes saved from Settings
  workspace/                   # tool-writable scratch space for general workflows
```

Conversation history lives under `data/conversations/` (legacy `conversations/` is auto-migrated on startup when `FLOAT_CONV_DIR` is unset). `blobs/` remains beside the repository root for now so existing tooling keeps working.

## Usage Notes

- `data/` is already gitignored - never check in user content.
- Use project-relative paths (`Path(__file__).resolve().parents[2] / "data" / ...`) so the same layout works on Windows and Linux.
- Documents/knowledge folder-ingest defaults to `data/files/workspace` so UI workflows stay inside the managed sandbox.
- Named workspace profiles typically live under `data/files/workspace/<slug>/` unless the user points them somewhere else.
- `data/modules/` is optional. When used, it acts as the local untracked override area for add-on package config, local skill markdown, and related module assets, while repo-shipped content lives under the tracked root `modules/` tree.
- `data/themes/` is the local storage area for user-created or edited visual themes saved from Settings.
- Imported nested sync copies can create deeper workspace roots beneath an existing workspace, and synced attachments may also keep custody paths like `workspace/sync/<source>/...` so source ownership stays visible after sync.
- Tools should request:
  - Read access for everything under `data/`.
  - Read/write for `data/databases/` when mutating structured stores.
  - Full read/write/delete for `data/workspace/`.
- RAG "memorize"/"forget" operations work on `data/databases/chroma/`: they add/remove vector rows keyed to the knowledge item while leaving the underlying file/text in `data/files` or the knowledge store untouched.
- `work_runs.sqlite3` is the device-local Activity ledger. It keeps one current receipt snapshot per stable id, append-only lifecycle rows, and one current indexed snapshot per provider-attempt/effect id backed by internal transition rows. Receipt `event_count` counts lifecycle rows; `attempt_count` and `effect_count` count indexed attempt/effect snapshots, and each child snapshot has its own `transition_count`. Effect rows retain only allowlisted classification, certainty, policy, approval/permission, identifier, target-redaction, and digest fields; they do not retain tool arguments or raw results. Provider rows likewise exclude prompts, raw responses, error messages, and checkpoint bodies. These child journals currently cover the scheduled-action runner, and a successful tool return records an acknowledgement rather than independent remote reconciliation. Exact resume context remains in Calendar compatibility state until a protected checkpoint capsule exists. The database has no age-based retention policy, foreign-key enforcement, or versioned migration framework yet. It is not a Knowledge/RAG or sync source. Because the compact receipt summary can still contain useful result text, treat the whole ledger as local user data rather than content-free telemetry.
- The backend seeds missing directories at startup; scripts can assume the tree exists.
- Before adding a new artifact type, update this document and the README so agents know where to write.

## Tracked vs Local Module Assets

- `modules/` at the repo root is the tracked home for shipped module assets.
- `modules/skills/` holds the base skills Float ships with.
- `modules/addons/{addon}/config.json` is for repo-shipped workflow add-on manifests/config that should be discoverable in git.
- `data/modules/addons/{addon}/config.json` is optional and is for local or user-installed add-on manifests/config when a machine wants untracked overrides.
- `data/modules/skills/` is optional and is for local or user-written skill markdown that should override or extend the repo-shipped `modules/skills/` guidance.
- If an add-on id exists in both places, the local `data/` copy should override the tracked repo copy.
- If a skill markdown id exists in both places, the local `data/` copy should override the tracked repo copy.
- Legacy `models/` at the repo root is documentation-only. Keep placeholder README files there, but store actual checkpoints in `data/models/`.

## Recent Migrations

- `calendar_events/` moved to `data/databases/calendar_events/` and is created automatically.
- Calendar, reflection, and Activity storage now resolve beneath the same `FLOAT_DATA_DIR`; `FLOAT_CALENDAR_DIR`, `FLOAT_REFLECTION_STORE`, and `FLOAT_WORK_RUN_STORE` remain explicit per-store overrides.
- The Chroma vector store now lives under `data/databases/chroma/` and respects `CHROMA_PERSIST_DIR`.
- Local model downloads default to `data/models/`; legacy `models/` folders are still detected for backward compatibility.
- `data/files/{uploads|screenshots|downloaded|workspace}` are created automatically so uploads, captures, and docs ingest share one sandbox.

## Workspace Notes

- The root workspace is the default unnamespaced local workspace.
- Additional workspace profiles are user settings, not separate databases.
- Sync can either merge into a target workspace or import another device's workspace as a nested synced workspace.
- Imported synced workspaces retain source-peer metadata so syncing back to the origin can avoid recursive trees.

Keep this document aligned with:
- `README.md`
- `docs/architecture_map.md`
- `docs/feature_overviews/device-sync-and-streaming.md`

Document any future moves in the repository docs at the same time so setup and storage guidance do not drift.
