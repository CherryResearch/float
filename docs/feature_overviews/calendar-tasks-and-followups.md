# Calendar, tasks, and follow-ups

Calendar, tasks, and follow-ups are the part of Float that turns a conversation into something scheduled, actionable, and reviewable later. Instead of keeping everything inside the current chat, Float can store an event, a reminder, or a task that carries actions forward into the future.

What makes this more than a normal calendar is that a scheduled item can still behave like part of the assistant. A follow-up can point back to a chat, carry a structured action list, or return later as a prompt that asks the user for review. Calendar owns the future schedule, Agent Console shows current-session work, and Activity retains compact device-local receipts after a run finishes.

Scheduled tools use the server catalog to enforce configured permission ceilings. Actions that require confirmation create an exact occurrence-bound `Approve once` or deny decision; edits to relevant schedule, policy, or action fields invalidate stale approval. Stop requests are cooperative and target one exact run. When dispatch may already have occurred, Float keeps an attention state for no-replay reconciliation rather than claiming that an external effect was rolled back.

Prompt actions persist a content-free checkpoint before provider work so a fresh process can safely resume the same run/message identity. Event deletion preserves retained Activity receipts and fails with clear guidance while either Calendar state or the durable ledger still reports active work. Recurring work currently coalesces missed overlap or downtime to the latest due occurrence instead of backfilling every missed time.

This system is increasingly unified, but it is still being refined. Provider or tool calls that do not cooperate cannot yet be hard-killed, and user reconciliation is an audit decision rather than independent remote-state verification. The public endpoint reference for the current shipped surface is `docs/api_reference.md`.
