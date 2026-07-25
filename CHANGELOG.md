# Changelog

## 2026-07-25 alpha patch

- Hardened tool approval and continuation so denied or completed requests stay
  terminal, repeated loops stop cleanly, and newer user turns supersede stale
  background work.
- Added safer deployment and device-sync foundations, including protected
  runtime data, build receipts, review checkpoints, and clearer ownership state.
- Expanded provider and runtime configuration for OpenAI-compatible servers,
  Tinker / Inkling, Anthropic-compatible, Gemini-compatible, and OpenRouter
  connections, with clearer model identity and reasoning/output controls.
- Made mobile and webcam attachments durable across refresh and reconnect, and
  improved Responses API visual follow-ups.
- Added deterministic conversation-versus-document import review for Markdown,
  text, JSON, and OpenAI export archives.
- Reorganized Settings around clearer Connections, Models, Visual data, and
  progressive-disclosure controls.
- Updated frontend dependency security and general backend/frontend reliability.

## 2026-05-02 alpha patch

- Tool discovery cleanup with curated help and recovery hints.
- Agent Console, retrieval, STT, and UI polish.
- Background reflection and conversation compaction surfaced more clearly.
- README/runtime catalog updated.
- General bugfixes.
