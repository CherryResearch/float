# Calendar, Tasks, and Sub-agents

For how these records are serialized into the shared knowledge/RAG layer, see `knowledge.md`; this document focuses on the upstream UI/API model.

The calendar module coordinates scheduled reminders, actionable tasks, and Celery sub-agents. This revision adds the concrete UI behaviours called out in the UI specification.

## Views & navigation
- **Default view**: Month grid with smooth infinite scroll vertically. Dragging the grid continues into adjacent months without pagination.
- **View toggles**: Tabs for Day / Week / Month. Switching views preserves the selected date and scroll position.
- **Colour system**: Tasks render in purple, events in green. Linked pairs (task ↔ event) display a pill connecting them.
- **Sidebar integration**: When the calendar occupies the main pane, the Agent Console switches into “tasks for the day” mode while the left sidebar keeps the conversation history.



## Event & task details
- Clicking an entry opens a modal drawer with:
  - Title, description, location, participants.
  - Linked chat messages (if created from a conversation) and linked media.
  - Buttons: `Mark complete`, `Snooze`, `Delete`, `Open in threads`.
  - Task-specific controls: recurrence, priority, sub-agent assignment, approval requirement.
- Inline editing: double-clicking a cell on week/day view creates a draft event; hitting enter saves via `POST /api/calendar/events`.
- Linked tasks/events show a chain icon. Clicking it reveals the related items stacked within the drawer.

### Quick event/task popup
The lightweight popup shown in `docs/ui design/event_popup_draft.png` replaces the browser alert we had before.

- **Layout**:
  - Left rail: name field plus a description bubble (multiline text). Description can shrink when empty; it inherits the frosted gradient block.
  - Right rail: stacked date chips for `Start`, `End`, `Review`, followed by an `Actions` pill. Each chip uses a compact label + value + icon row.
  - Optional chips (End/Review) render as a floating `+` bubble until populated; once set they show the date and expose a contextual `×` to clear.
- **Date picking**:
  - Clicking the start chevron opens the mini calendar; typing in the field stays supported.
  - The popup tracks the last-focused chip. After setting Start, the next click targets End, then Review—without forcing the calendar to close each time.
  - Keyboard: `Tab` cycles fields; `Enter` confirms. Pressing `Esc` collapses the mini calendar but keeps the popup open.
- **Actions field**:
  - Opens a structured, ordered action list editor (so tasks are actionable, not just notes).
  - Tool actions validate against `/api/tools/specs` before saving (required fields must be present).
- **Tool-backed actions**:
  - The scheduling tool exposes create/edit/delete operations plus full metadata (title, description, type, start/end, recurrence, status, linked ids, participants, location, approval flags). Editing or viewing any scheduled action reuses the same pop-out so operators can change payloads without switching contexts; the same surface is reachable from the agent-console tool cards via the Edit/Schedule buttons.
- **Styling**:
  - Foreground bubbles use radial gradients fading to transparent, then a subtle linear gradient extending beyond the surface to mimic depth.
  - Each pill has a semi-transparent white overlay with light-to-transparent radial shine to achieve the matte glass look.
  - The entire popup floats above the calendar without shifting layout; background blur stays consistent with the rest of the UI.
- **State handling**:
  - Start must exist; End/Review are optional and collapsed by default.
  - Form autosaves drafts locally so closing/reopening the popup restores the last edit.

### Unified item model (event / task / note)
Float stores calendar items in one schema and derives "type" from which fields are populated:

- **Event**: date-bound (has `start_time`, may have `end_time`), may also carry actions.
- **Task**: actionable (has one or more `actions`), may also be date-bound (scheduled tasks).
- **Note / memory**: informational (no actions) but can still be anchored to a time window.

### Actions (tool + prompt)
`actions` is an ordered list. Each action is one of:

- **Tool action**: `{ kind: "tool", name, args, prompt? }` where `args` follow the tool schema; `prompt` is an optional follow-up prompt to run after the tool completes.
- **Prompt action**: `{ kind: "prompt", prompt }` which injects a user prompt into the chat workflow at the scheduled time (best-effort automated response).

The backend executes scheduled actions via `POST /api/calendar/events/{id}/run` (manual) and the scheduled runner (automatic). After execution, items move into `status="prompted"` so the operator can acknowledge completion from the "upcoming tasks" panel.


## Timeline & reminders
- Tasks can be accelerated to sub-minute cadences for system automation. UI displays a warning badge when cadence < 1 minute.
- Missed tasks (e.g., during downtime) populate a “Catch up” list surfaced in the sidebar. Bulk `Approve all` executes outstanding sub-agent actions.

## API surface
| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/calendar/events` | `GET` | Fetch events/tasks for a date range with filters for status, sub-agent, or folder. |
| `/api/calendar/events` | `POST` | Create or update an event/task. Accepts `{ type: event|task, datetime, duration, colour?, linked_ids[], subagent_id?, approval_required? }`. |
| `/api/calendar/events/{id}` | `PATCH` | Modify metadata (title, schedule, recurrence, links). |
| `/api/calendar/events/{id}` | `DELETE` | Soft-delete event/task. |
| `/api/calendar/events/{id}/complete` | `POST` | Mark task complete; logs outcome and triggers follow-up actions. |
| `/api/calendar/snooze` | `POST` | Snooze an event/task for a configurable duration. |
| `/api/calendar/missed` | `GET` | List missed tasks requiring approval. |

## Sub-agent coordination
- Calendar tasks queue Celery jobs tagged with the assigned sub-agent role. Jobs reference conversation context plus linked media.
- Approval workflows integrate with the agent console: when a sub-agent proposes an action, the event stream includes `calendar_action` metadata for inline approval.
- Reassigning a task to another role updates both the Celery queue routing key and the associated settings entry.

## Storage ties
- Calendar exports a `calendar/events.jsonl` snapshot in the object store nightly for backup.
- Linked resources store relative paths to conversations or media assets so offline devices can resolve them locally.

Align future calendar interactions with these behaviours so the UI and backend stay consistent.
