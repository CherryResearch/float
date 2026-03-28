## Conversations export/import formats

This repository supports on-demand conversation export and import via API:

- Export (markdown): `GET /api/conversations/{name}/export?format=md`
- Export (text): `GET /api/conversations/{name}/export?format=text`
- Export (json): `GET /api/conversations/{name}/export?format=json`
- Export all (zip): `GET /api/conversations/export-all?format=md|json|text`
- Import (markdown/json): `POST /api/conversations/import`

### Markdown export format

The markdown export is a readable transcript with minimal structure:

```
# Conversation Export
- name: <conversation_name>
- id: <uuid>
- display_name: <title>
- created_at: <iso>
- updated_at: <iso>
- message_count: <n>
- exported_at: <iso>

## Messages
### [user] id=<message_id> ts=<timestamp>
<message text>

### [ai] id=<message_id> ts=<timestamp> status=<status>
<assistant text>

#### thoughts
thoughts: {tokens} tokens, {seconds}s, {responses} responses: <concatenated thought stream>

#### tools
- [x] <tool_name> (<status>) args=<json> result=<json>
```

Notes:
- Roles are `user`, `ai`, `system`, or `tool`.
- The `#### thoughts` block is optional and is ignored on import.
- The `#### tools` block is optional and is ignored on import.
- `thoughts: ...` stores a concatenated stream and summary stats.

Export options:
- `include_chat=true|false` (default true)
- `include_thoughts=true|false` (default true)
- `include_tools=true|false` (default true)

Export-all notes:
- The zip includes one file per conversation, named after the stored conversation key.
- Folder paths in conversation names are preserved inside the zip.

### JSON export format

The JSON export returns a payload:

```
{
  "name": "<conversation_name>",
  "exported_at": "<iso>",
  "message_count": <n>,
  "metadata": { ... },
  "messages": [ { "role": "...", "text": "...", ... } ]
}
```

### Import format

Markdown import accepts the export format above. Messages are created from
`### [role]` headers and the following text block until the next header.
Thoughts blocks are ignored.

JSON import accepts either:
- a list of message objects, or
- an object with a top-level `messages` list.
