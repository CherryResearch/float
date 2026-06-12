Use this skill for browser control, desktop control, camera capture, capture promotion, and approval-gated host actions that live under the `computer_use` workflow module.

# Computer Use

## Start state
- Begin with `help` or `tool_info` only when the capability is genuinely unclear. Do not spam `tool_help` on every turn.
- For browser or desktop work, start with `computer.session.start` and reuse the returned `session_id`.
- Prefer `runtime="browser"` for webpage tasks and `runtime="windows"` for host desktop tasks.

## Core loop
- Observe before acting when state may have changed: use `computer.observe`.
- Act in small batches: one click, one type, one keypress, or one navigation at a time when outcomes are uncertain.
- Re-observe after page transitions, modal opens, scroll jumps, or anything that changes layout.
- If a tool already returned an error or denial, treat it as resolved and adjust the plan. Do not keep requesting the same approval.

## Browser flow
- Use `computer.navigate` instead of the legacy `open_url` alias for new work.
- Use `computer.observe` to confirm page state before targeting coordinates or selectors.
- If a site opens a new window or tab, inspect window state before continuing.

## Desktop flow
- Use `computer.windows.list` before focusing a window with an uncertain title.
- Use `computer.windows.focus` before typing into desktop apps.
- Use `computer.app.launch` only after a session exists.

## Camera and captures
- `camera.capture` returns a transient image capture from the client device, not the backend host.
- The model cannot see a camera image unless that image is actually attached into the conversation or forwarded in a follow-up request.
- Recent image follow-ups should reuse the latest relevant image when no new image is explicitly supplied.
- Use `capture.list` to inspect recent transient captures.
- Use `capture.promote` when the image needs to survive beyond the transient capture window.
- Promotion is the bridge from transient capture state into durable attachment/memory workflows. Do not treat every capture as something that should be promoted automatically.

## Image reasoning
- Treat image understanding as attachment-based, not capability-flag-based. A vision-capable model still needs the image attached in the actual request.
- If the user asks about "that image", "this screenshot", or a direct follow-up right after an image turn, prefer the most recent attached image unless the user names a different one.
- For compare tasks, make sure two images are attached or recalled before answering.

## Context discipline
- Keep commentary between obvious tool steps brief.
- Summarize tool outcomes instead of pasting entire help payloads back into the model context.
- Only fetch richer tool docs when a missing argument, runtime restriction, or workflow branch is blocking progress.

## Host actions inside this module
- `shell.exec`, `patch.apply`, and `mcp.call` are part of the same module family even though they are higher-risk tools than observation or capture.
- Inspect before mutating, keep commands targeted, and prefer the smallest verifiable change.
- If the user only needs UI/browser help, do not escalate into shell or patch tools just because the module allows them.
