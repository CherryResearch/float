chat and input is the main way a person talks to float. It covers typing, speaking, attaching files, sending images, and using the basic controls that shape a message before it is sent. The goal is to make one clear entry point whether the user wants a quick text exchange or a richer multimodal conversation.

In practice, this means the composer can hold longer prompts, keep drafts, attach media, and carry the context that tells float which workflow or runtime path should be used. A user mostly experiences this as a message box with attachments, voice controls, and send behavior that feels flexible rather than rigid, while the system quietly keeps the message tied to the right conversation and stored files.

A small set of inline commands shapes that flexibility. Typing `%{toolname}` (e.g. `%remember …`) starts a tool invocation, `./` launches file search, `//` starts an embedded-memory lookup, and `.//` blends the two result sets. Suggestions appear alphabetically like a terminal Tab completion and render with a hyperlink-like affordance inside the composer. Hitting Tab cycles through matches, Enter keeps the selected suggestion, and backspacing immediately after a linked argument clears the link while leaving the typed text untouched.

| Trigger | Behavior |
| --- | --- |
| `%{toolname}` (for example `%remember ...`) | Flag the line as a tool call and append the rest of the prompt as the payload; the suggestion list stays sorted alphabetically so Tab cycles predictably before you finish typing arguments. |
| `./` / memory search and `//` / file search | Inline lookups for managed files or stored memories. Matching results appear as link-like tokens so you can click them to insert structured context, and Tab jumps through matches in lexicographic order. |
| `.//` | Run a blended search across both files and memories when a single lookup should pull from either source without switching commands. |

Inline tokens stay linked until you delete the trailing space or explicitly backspace through the highlighted text, which unlinks it so you can edit the raw words again.

This feature is already central to how float works, but live voice and some multimodal behaviors still need more real-world verification than plain text chat. The public references for the current shipped surface are `README.md`, `docs/architecture_map.md`, and `docs/api_reference.md`.
