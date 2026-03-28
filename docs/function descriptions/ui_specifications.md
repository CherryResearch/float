# UI Surface Specification & Functional Mapping

This document now lives alongside the functional specifications so that every UI element has a clearly linked behaviour or API surface.

## Functional excerpts to relocate into feature docs
| UI area | Functional requirement from the sketch | Target functional doc | Notes |
| --- | --- | --- | --- |
| Chat composer | Live mode toggle, voice recorder, send button, media attachment picker, role selector, API/local toggle, dynamic textarea growth | `function descriptions/chat_interface.md` | The new chat interface doc defines input events, upload flows, and related API routes. |
| Conversation history sidebar | Tree folders, rename/delete, metadata preview, sorting and search | `function descriptions/memory.md` + `function descriptions/object_storage.md` | History draws from the object store and uses the memory API for rename/delete mutations. |
| Threads top bar | Topic-count slider, manual/auto toggle, re-embed control, folder filter, editable titles, collapsible previews, spool saving | `function descriptions/threads.md` | Threads doc now covers UI controls, spool persistence, and related endpoints. |
| Calendar tab | Day/week/month toggles, colour-coded tasks vs. events, linked-item popover with edit controls | `function descriptions/calendar_tasks_and_subagents.md` | Calendar doc captures the UX interactions and the Celery/task integration. |
| Thoughts panel | Inline approve/deny/edit buttons, selection linking thoughts ↔ chat messages with overlay lines, streaming emphasis | `function descriptions/workflows.md` | Workflows doc describes the approval flow, selection state, and streaming events. |
| Media gallery | Zoom levels, metadata hover, favourites/folders reuse | `function descriptions/memory.md` + `function descriptions/object_storage.md` | Media viewer references the shared storage layout and metadata schema. |
| Knowledge tab | Table view default, graph toggle, hypergraph semantics with threads + spools linkage | `function descriptions/memory.md` | Knowledge surface leans on the knowledge graph and embedding stores described there. |
| Settings | Role editor, workflow-specific model picker with availability indicator, sync/device management | `function descriptions/models_directory.md` + `function descriptions/workflows.md` | Model picker and role settings are surfaced within the workflow + models docs. |

## Visual design reference

### Core idea
A mix of flat design, frosted-glass gradients, and UI elements “floating” over the main chat window. The overall design should be modern, immersive, graceful, sleek, and fun. Be consistent with documentation: specify choices/methods, provide reasoning, note alternatives and attempt history, and keep functionality linked to elements.

### Colour palette (light and dark themes)
- White: `#ffffff`, `#e8fae8`
- Mint green: `#86eaa0`
- Pear green: `#21b228`
- Black: `#000000`
- Purple: `#630ac3`, `#340865`
- Indigo: `#390892`
- Lavender: `#e4d9f3`, `#b29ed9`

Stick to these colours specifically, but use blends and gradients to interpolate between them—think matte diffuse light, frosted glass, marble swirls. In light mode the gradients blend white → mint; in dark mode black → purple. Solid black text and deep violet links contrast against lavender borders and pear-green buttons (in dark mode this inverts: black background with diffuse purple gradients, details in green with white text).

Other palettes (blues, oranges, pinks) can be defined later using the same formulas. Generate core UI elements programmatically so different themes stay consistent. Some themes may keep the frosted-glass look while others go flat.

### Layering
- Background pane contains the theme gradient.
- Main content is centred with subtle/invisible borders.
- Chat mode text is boxed by a right-side scrollbar and a faint gradient fade on the left.
- Gradients rely on soft lights; experiment with shears/warps/blurs rather than simple gradients to achieve the marbled effect.

### Layout & core elements
- Main content is contextual: defaults to chat but can host live feeds or knowledge panes. Keep it roughly square; for a 1920×1080 screen, target ~1080px width/height. On tall displays, close sidebars by default and let main content dominate the height.
- Floating interface: two sidebars, a text entry bubble, and (currently) a top bar. Elements are rounded rectangles with subtle drop shadows and matte reflections.
  - Hide/show elements via a circular button; click or hover (1s) toggles state. Closed elements stay visible but muted (lavender text for titles, lighter button).
  - Sidebars sit slightly off-screen (<1 cursor width) so they do not crowd the content. Chat box scales up with content but never exceeds ~60% screen height. Top and bottom bars stay within main-content width with padding.
  - Defaults: left sidebar = history, right sidebar = thoughts, main content = chat, top bar = chat/knowledge/settings tabs. Future enhancement: drag modules between top bar and sidebars with contextual scaling; chat entry follows whichever pane hosts chat.
- Phone/portrait interface collapses side/top bars into tab selectors. Toggling a sidebar opens it to ~80% width; top bar stacks behind if both are open. Only one pane is visible at a time. Clicking outside the pane returns to main content.

### Typography
- Use a clean sans-serif font, mostly lowercase labels (Float, history, knowledge). Acronyms and proper names stay capitalised.
- Text is primarily black or white (depending on theme) with pear/violet/mint/lavender accents to create hierarchy. Purple works for links/titles, green for frequently used buttons.
- Minimise hard borders around text boxes; colour the text cursor.
- Chat container may use a secondary, less rounded/terminal font.

### Component visuals (non-functional cues)
- Messages align left (Float) or right (user) and take ~⅔ width with slight overlap; no individual bubbles required.
- Maintain smooth scrolling in chat, history, media, knowledge views.
- When thoughts or messages are selected, apply rounded border highlights; lighten related items.

Refer to the archive images (ui sketch, ui_draft, palette examples) for additional art direction cues.
