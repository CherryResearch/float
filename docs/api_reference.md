# API Reference

This document is the thin public endpoint reference for the current alpha snapshot.
It is intentionally narrower than an internal route dump or design note.

Status labels used below:

- `Alpha public`: part of the normal user-facing product surface today.
- `Alpha preview`: present and usable, but still early enough that copy, flow, or edge-case behavior may change.
- `Dev-only`: intended for development, diagnostics, or manual verification rather than ordinary use.
- `Planned`: mentioned for context elsewhere, but not part of the current public contract.

## Notes

- Endpoint paths below are shown with the `/api` prefix used by the app.
- This file documents the current surface, not every internal helper route.
- Planned routes are called out explicitly so they are not mistaken for active behavior.

## Chat and Conversations

### `Alpha public`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/chat` | Submit a chat turn in the selected runtime mode. |
| `POST` | `/api/chat/continue` | Continue after tool results, approvals, or recoverable tool errors. |
| `GET` | `/api/conversations` | List saved conversations and folders. |
| `GET` | `/api/conversations/{name:path}` | Load one conversation by path-like name. |
| `POST` | `/api/conversations/{name:path}` | Save or overwrite a conversation by path-like name. |
| `POST` | `/api/conversations/{name:path}/rename` | Rename a conversation. |
| `DELETE` | `/api/conversations/{name:path}` | Delete a conversation. |
| `POST` | `/api/conversations/import` | Import conversation data from supported export formats. |
| `GET` | `/api/conversations/{name:path}/export` | Export one conversation in a supported format. |
| `GET` | `/api/conversations/export-all` | Export all conversations as a bundle. |

### `Alpha preview`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/conversations/reveal/{name:path}` | Ask the host to reveal a conversation in the local filesystem when supported. |
| `POST` | `/api/conversations/import/preview` | Preview a conversation import before applying it. |
| `GET` | `/api/conversations/{name:path}/suggest-name` | Return an automatically suggested conversation name. |

## Knowledge, Memory, and Files

### `Alpha public`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/memory` | List stored memories. |
| `POST` | `/api/memory/{key}` | Create or update a memory entry by key. |
| `DELETE` | `/api/memory/{key}` | Delete a memory entry by key. |
| `GET` | `/api/knowledge/list` | List knowledge items and related metadata. |
| `POST` | `/api/knowledge/add` | Add text or file-backed knowledge to the store. |
| `GET` | `/api/knowledge/query` | Query the knowledge store and retrieval layer. |
| `GET` | `/api/knowledge/file/{doc_id}` | Serve a local knowledge file through the backend. |
| `GET` | `/api/knowledge/reveal/{doc_id}` | Reveal a local knowledge file in the host filesystem when supported. |
| `GET` | `/api/attachments` | List attachments known to the app. |
| `POST` | `/api/attachments/upload` | Upload an attachment through the backend-managed storage path. |

### `Alpha preview`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/memory/graph` | Return the current memory / graph visualization payload. |
| `POST` | `/api/memory/search` | Search memories directly by query. |
| `GET` | `/api/threads/summary` | Return the current thread summary state used by the Threads UI. |
| `POST` | `/api/threads/generate` | Generate or refresh semantic threads. |
| `POST` | `/api/threads/search` | Search within generated thread structures. |
| `POST` | `/api/threads/rename` | Rename or relabel generated thread groups. |

## Tools and Agent Console

### `Alpha public`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/tools` | List built-in tool metadata. |
| `GET` | `/api/tools/specs` | Return the current tool schema/spec payload used by the frontend. |
| `POST` | `/tools/register` | Register an allowed built-in tool for invocation. |
| `POST` | `/tools/invoke` | Invoke a registered tool. |
| `GET` | `/api/agents/console` | Load the current Agent Console snapshot. |
| `GET` | `/api/stream/thoughts` | Stream thought/tool activity for the Agent Console. |

### `Alpha preview`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/tools/catalog` | Return tool catalog metadata used by the UI. |
| `GET` | `/api/tools/catalog/{tool_name}` | Return one tool catalog entry. |
| `GET` | `/api/tools/limits` | Return current tool/runtime limit metadata. |
| `POST` | `/tools/propose` | Submit a tool action that requires review. |
| `POST` | `/tools/decision` | Approve, edit, or reject a proposed tool action. |
| `POST` | `/tools/schedule` | Schedule a tool action for later execution. |

## Calendar and Scheduling

### `Alpha public`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/calendar/events` | List calendar events and tasks. |
| `GET` | `/api/calendar/events/{event_id}` | Load one event or task. |
| `POST` | `/api/calendar/events/{event_id}` | Update an event or task. |
| `DELETE` | `/api/calendar/events/{event_id}` | Delete an event or task. |
| `POST` | `/api/calendar/events/{event_id}/prompt` | Run or queue the reminder/prompt action for one event. |
| `POST` | `/api/calendar/events/{event_id}/run` | Execute an event-linked action directly. |
| `POST` | `/api/calendar/reminders/flush` | Trigger reminder catch-up used by the UI on load. |

## Settings, Models, and Runtime

### `Alpha public`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/settings` | Read persisted runtime and UI settings. |
| `POST` | `/api/settings` | Update persisted runtime and UI settings. |
| `GET` | `/api/openai/models` | Probe the configured OpenAI-compatible provider for model inventory. |
| `GET` | `/api/transformers/models` | List available direct local transformer models. |

### `Alpha preview`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/rag/status` | Return retrieval / embedding runtime status for the knowledge UI. |
| `GET` | `/api/models/registered` | Return the local registered-model list. |
| `POST` | `/api/models/registered` | Add a local registered-model alias. |
| `DELETE` | `/api/models/registered/{alias}` | Remove a local registered-model alias. |

## Voice and Live Streaming

### `Alpha preview`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/voice/tts` | Generate speech audio through the configured TTS path. |
| `POST` | `/api/voice/connect` | Bootstrap a live voice session. In OpenAI Realtime mode this returns the browser-facing connection details; in LiveKit mode it returns the fallback room/token flow. |
| `POST` | `/api/voice/stream` | Legacy or fallback worker-backed voice stream endpoint. |

### `Dev-only`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/test-prompts` | List built-in test prompts used from the development panel. |
| `POST` | `/api/test-prompts/{name}` | Execute one built-in development test prompt. |

## Trusted-Device Sync

### `Alpha preview`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/sync/overview` | Return current device, visibility, and saved/paired peer state. |
| `POST` | `/api/sync/pair` | Pair this device with a trusted peer using the current pairing flow. |
| `POST` | `/api/sync/plan` | Preview pull/push differences before applying sync changes. |
| `POST` | `/api/sync/apply` | Apply a selected pull/push sync action. |
| `POST` | `/api/sync/manifest` | Exchange sync manifests between trusted peers. |
| `POST` | `/api/sync/export` | Export selected sync data for a trusted peer. |
| `POST` | `/api/sync/ingest` | Ingest approved sync data from a trusted peer. |

## Planned, Not Current

These are mentioned elsewhere in public docs for context, but should not be treated as active public API guarantees:

- `/api/stream/responses` for assistant text deltas
- broader live session handoff beyond the current voice bootstrap flow
- background or gateway-style sync beyond the current trusted-device preview model
