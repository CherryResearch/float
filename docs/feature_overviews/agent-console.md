# Agent Console and Activity

Agent Console is Float's live and recent operations view. It brings together
several related runtime surfaces without pretending that they are all model
subagents:

- chat and tool activity associated with a conversation;
- bounded reflection and scheduled prompt runs;
- Celery task chains and worker health;
- the opt-in background-reflection supervisor.

Conversation branches and genuine subchats can link back to saved chats.
Celery workers and task chains are execution infrastructure, not conversational
agents, and Float labels them accordingly.

Controls are capability-specific. A Celery chain can receive a real Stop request
through task revocation, while a Calendar action accepts a cooperative stop
request for one exact run. Pause, resume, and redirect are not shown as runtime
controls unless the responsible worker can honor them. A late stop request does
not claim that an already-dispatched external effect was rolled back.

Background work is split by responsibility. Calendar owns start times, time
zones, recurrence, and series limits. Patience owns the stop condition and
safety budget for each occurrence. Execution policy owns effort, permissions,
sandboxing, and whether bounded subagents are allowed. Ownership metadata links
a job back to its event, conversation, message, parent job, and parent agent.

Calendar shows future work, Agent Console shows current-session work, and
Activity records what ran. Activity uses a device-local SQLite ledger, so a
compact receipt remains available after its Calendar event is deleted. Active
events cannot be deleted until their run reaches a terminal state. Activity can
expand metadata-only lifecycle, attempt, and effect evidence without exposing
prompt bodies or raw tool arguments.

Provider restart recovery is deliberately conservative. Prompt-only work can
resume from a content-free checkpoint under the same run and message identity.
Uncertain external effects are not replayed automatically; Activity instead
offers a no-replay reconciliation decision when user evidence is required.

The background-reflection supervisor remains bounded and opt-in. Legacy Settings
modes are accepted for compatibility, but user-facing labels describe budget and
termination posture rather than treating overnight or two checks as distinct
kinds of reasoning. Richer patience and execution policies remain available to
agent runtimes that support them.

Agent Console records are primarily live process state. Activity is the durable
device-local receipt surface; neither surface is a remote audit service or an
independent verification of provider-side effects.

Implementation-facing runtime and request-flow details are documented in
`docs/architecture_map.md`.
