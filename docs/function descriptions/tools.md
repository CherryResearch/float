# Tools

This document defines what a tool is in float and what the current tool set is for.

A tool should do one bounded thing well: read, write, search, create or edit something, or run a managed helper task.
float's tools are executed through a python API backend. any general tools are sandboxed by default.
file access is limited to an internal workspace folder, inside of the data directory of a given float deployment.

Each tool should have a plain-language description, clear input shape, scope, approval rule and result shape,

## Current built-in tools

| Tool | Status | Function | Scope | Result |
| --- | --- | --- | --- | --- |
| `tool_help` | live | Explains available tools and their arguments. | Tool metadata only. | Tool summary or schema help. |
| `search_web` | live | Searches the web for pages about a topic. | Public web search. | Query, result count, and a list of titles, URLs, and snippets. |
| `crawl` | live | Reads one known URL. | One page per call. | Clipped page text. |
| `open_url` | stub | Opens or hands off a URL for viewing. | Browser/UI action. | Confirmation only. Not page content. |
| `read_file` | live | Reads a local text file. | Files under `data/`. | File contents as text. |
| `write_file` | live | Writes a local text file. | Files under `data/workspace/`. | Write confirmation. |
| `remember` | live | Saves or updates a durable memory entry. | Shared app memory. | Success status. |
| `recall` | live | Reads or searches stored memory. | Shared app memory and retrieval index. | Exact match or search results. |
| `generate_threads` | live | Rebuilds topic threads from saved conversations. | Stored conversations. | New thread summary snapshot. |
| `read_threads_summary` | live | Reads the latest saved thread summary. | Stored thread summary. | Current thread summary. |
| `create_task` | live | Creates or updates a task or event. | Local task/calendar data. | Saved task or event record. |
| `memory.save` | legacy | Older memory-writing alias. | Shared app memory. | Compatibility result. |

## Similar tools

### `search_web`, `crawl`, and `open_url`

- `search_web` finds candidate pages from a query.
- `crawl` reads a page when the URL is already known.
- `open_url` opens or hands off a page for viewing. it is not a content-reading tool today.

### `remember` and `recall`

- `remember` stores or updates something float should keep.
- `recall` fetches something float already knows.

### `generate_threads` and `read_threads_summary`

- `generate_threads` creates a fresh thread snapshot from conversations.
- `read_threads_summary` reads the last saved snapshot.

### `read_file` and `write_file`

- `read_file` reads text from the managed data area.
- `write_file` writes text into the managed workspace area.

## Current scope and limits

### Web tools

- `search_web` is the discovery step.
- `crawl` is the page-reading step.
- fetched web content should be treated as untrusted text, not instructions.
- `open_url` is still a stub and should be labeled that way in prompts and UI.

Current behavior:

- `search_web` returns structured search results.
- `crawl` fetches a single page and clips the response.
- neither tool is a full browser session.

### File tools

- `read_file` is limited to `data/`.
- `write_file` is limited to `data/workspace/`.
- current file tools are text-focused.
- there is no general directory listing tool yet.
- there is no targeted edit tool yet.

### Memory tools

- memory is shared app state, not just per conversation.
- memory is durable.
- some memory can also be mirrored into retrieval.
- sensitivity rules matter; protected and secret values should not be casually exposed outward.

### Thread tools

- threads are generated from saved conversations.
- the summary is a snapshot and can become stale.
- reading the summary is not the same as recalculating it.

## Safety

### Validation

- tool arguments must be validated before execution.
- unexpected fields should be ignored or rejected, not silently trusted.
- tool descriptions should match the accepted argument shape closely.

### Prompt injection and hostile content

Anything fetched from the web, read from a file, or returned by another tool can contain hostile instructions.

Required behavior:

- treat fetched or read content as data, not instructions.
- sanitize arguments before execution.
- keep the http client bounded and explicit.
- do not widen filesystem or network scope based on content returned by another tool.

### Approval and audit

- riskier tools should require approval.
- every run should be visible in the agent console.
- long-running tools should expose status and allow cancellation.
- tool descriptions in docs, prompts, and UI should mean the same thing.

## Planned additions

### Web tools

Near-term work:

- improve `search_web`, `crawl`, and `open_url`.
- harden against prompt injection in fetched content.
- harden the http client path the same way.
- support batch fetches.
- add structured extraction helpers for common formats.

### Workspace tools

Planned additions:

- directory listing
- file discovery and search
- file stats
- targeted file edits
- parsing helpers for json, csv, html, and similar formats

These should stay limited to the managed workspace or other clearly declared roots.

### Python sandbox

Planned behavior:

- run python in an isolated sandbox
- stream stdout and stderr into the agent console while it runs
- allow expanding the live terminal into a popup
- allow the user to stop the run

Long term, this should be able to move into a fuller vm-backed environment.

### Computer-use tools

Planned behavior:

- allow the user to grant control of an app or desktop session
- build on the improving vision pipeline
- keep approval explicit
- prefer a vm or similarly isolated environment before broad host control

## Tool description style

Tool descriptions should describe user-visible meaning first.

Prefer:

- "`search_web` searches the web for candidate pages."
- "`crawl` reads one known URL."
- "`open_url` opens a page for the user; it does not read page contents."
- "`read_file` reads a text file from float's managed data area."
- "`write_file` writes a text file into float's workspace."

Avoid leading with transport or framework details when describing a feature.

## Related docs

- `docs/function descriptions/chat_interface.md`
- `docs/function descriptions/workflows.md`
- `docs/data_directory.md`
- `docs/internal/tool_catalog_and_custom_tools.md`

## Implementation references

- `backend/app/tools/`
- `backend/app/tool_specs.py`
- `frontend/src/components/ToolEditorModal.jsx`
