# Graph undo preview

October 19, 2026 (UTC). Reintegrated from the October 1 reviewed preview; preserves the current graph.query implementation.

Work History graph undo restores the older captured values and the complete role set of every selected claim in one SQLite transaction. It combines overlapping selected actions, creates endpoint nodes before roles, and rolls back if an endpoint, record, role key, integrity check or exact postcondition is invalid. A deleted node cannot silently strip roles from an unselected claim.

Existing newer-action conflicts still require explicit force. The graph also checks its current records against the recorded action; an unrecorded edit fails closed. Force captures current state and still rejects a race after that capture. Restored changed rows get fresh monotonic update timestamps while preserving original creation time and provenance. Sync remains a separate newer-record-wins merge.

The undo result and stored action result include graph integrity counts: nodes, claims, roles, projectable claims, roleless claims and unresolved endpoints. Counts refer to the whole post-restore graph, not just changed records or the UI projection. Literal-valued claims remain durable even when the existing node-link projection does not display them.

False, zero, empty strings/containers and explicit JSON null retain their types. An additive `value_present` role-snapshot flag distinguishes an explicit null literal from an absent value. Legacy ordinary literals infer this flag; a legacy null-only role without a presence flag is ambiguous and fails closed rather than inventing a value. An explicitly captured empty role array is preserved, including on redo of existing node-deletion behavior; this is reported as a roleless claim and is not an implicit data repair.

## Verified scope and limits

On the October 19 main source baseline, the original synthetic rename test reproduced the `noop` failure before integration. After integration, 262 focused offline tests passed: graph undo and graph.query (120), plus graph store/payload/tool/routes/query privacy/query prompt and action history/instance sync (142). These validate corrected value/topology, undo/redo, multi-action, conflict, cascade protection and rollback behavior while retaining graph.query. Both verification runs reported zero network connection attempts. Black, isort, flake8 and whitespace checks passed for the changed Python files. No production graph data or real model/provider requests were used.

Only the selected graph restore is atomic. Mixed-section undo and the JSON Action History files do not share the graph database transaction; crash recovery across those stores remains separate. This change does not repair already incomplete historical snapshots, change graph rendering, add aliases/merging, or implement a repair preview. Intended-checkout/live UI validation and full release checks remain pending.
