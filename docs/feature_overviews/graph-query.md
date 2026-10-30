# Complete graph retrieval

`graph.query` reads retained claims from the existing graph store. It complements
`recall` semantic/hybrid retrieval with exact relation, role, and time filters.
It does not use the drawing projection or an embedding service.

This tool is local-only. After signature verification, it refuses all external
or unknown data-policy destinations before reading the store, even when remote
querying is enabled. Existing graph claims do not enforce sensitivity inheritance
from related memory, and arbitrary metadata cannot safely establish permission
to export literals or node attributes. A missing policy is also denied: signed
direct callers must enter an explicit local data-policy scope. Saved raw graph
results cannot be replayed through chat/continuation to an external or unknown
destination. Once graph data enters a conversation, a monotonic local-only
metadata flag keeps subsequent chat, continuation and direct generation on local destinations,
even after raw tool events are replaced. Branches and native JSON roundtrips
preserve it. Deterministic compaction carries the
restriction into the target conversation; LLM compaction fails closed until it
has a verified destination-aware path. Compaction tools cannot return marked
source content to an external or unknown destination. This is scoped protection
for these supported paths, not general taint tracking of manually copied text
or arbitrary tool-mediated derivatives.
The restriction depends on intact canonical metadata: manual deletion/corruption
of sidecars and plain-text/Markdown transfers do not preserve these guarantees.
It does not relax existing recall privacy rules.

Reflection and scheduled model generation refuse graph-marked conversation
context, including local-provider runs. Their saved notes, Calendar results and
retrieval mirrors do not yet retain this restriction. Recent-thread reflection
selection skips these conversations; explicit requests report the limitation.
Continue graph work in its original conversation with a local model instead.
Ordinary unmarked background work remains available. Before reusing older
reflection notes, Float follows their retained conversation and reflection
references and checks the current restriction, including sources referenced
through earlier notes. Missing, corrupt or cyclic ancestry prevents reuse. This
does not erase older notes or reconstruct provenance that was never recorded.

## Current claims and audit history

`mode="current"` is the default. Before counting or applying `limit`, it requires:

- `status` is `active` (case/outer whitespace normalized); rejected, inactive,
  cancelled, superseded, expired and other lifecycle statuses are excluded
- `epistemic_status` is observed, asserted, scheduled, predicted or hypothesized;
  cancelled, superseded and unknown stored values are excluded
- Any supplied validity bounds include the single `current_at` query timestamp:
  `valid_from <= now < valid_to`. A missing bound is open; future starts and
  expired intervals are excluded

This is eligibility of retained claims, not proof of truth or user adoption.
Predictions and proposals keep their epistemic status and source metadata. An
unaccepted assistant proposal is never rewritten into an accepted decision.
Lifecycle comes from the stored status fields, not arbitrary provenance metadata.
The reader does not infer relationships between different claim IDs.

`mode="audit"` includes all retained lifecycle and epistemic statuses unless an
explicit filter narrows them. Use it for rejected alternatives and historical
validity. It has the same local-only guard and response bounds. No automatic
RAG or `recall` behavior changes are included.

## Query

- `mode`: `current` (default) or `audit`
- `node_id`: incident node in any role
- `predicate`: exact stored relation name
- `role_nodes`: up to eight role-name/node-ID pairs; every pair must match
- `epistemic_status`: optional exact status; current eligibility still applies
- `limit`: 1–100 claims, default 50, in stable ascending claim-ID order
- Filter strings, including role names/node IDs: at most 512 characters
- Response: at most 1,000 roles and 262,144 UTF-8 JSON bytes. An oversized result
  raises an explicit error; lower the claim limit or narrow the query. A single
  oversized claim cannot be retrieved through this bounded tool. Roles, literals
  and source fields are never silently clipped

Filters run in SQLite before limiting. A read transaction returns complete claims,
all their retained roles (including typed literals, explicit null, false, zero,
repeated participants and role metadata), and referenced nodes. `value_present`
distinguishes a retained JSON null from
a node-only role; ingestion behavior is unchanged. Claims carry source kind,
source reference, provenance metadata, epistemic status and validity fields.
The matched count and truncation flag distinguish a bounded page from a complete
answer. There is no pagination or natural-language node-name resolution in this
small first version. A missing node/predicate gives an empty result. Invalid
filters (including unknown keys, boolean dates/limits before normalization),
signatures and an unavailable store raise explicit errors.

## Model continuation

Successful graph results bypass the generic tool-prompt excerpt formatter so
literal roles, nested provenance and epistemic status reach the local answering
model intact. The aggregate graph JSON for a continuation, including event
wrappers and arguments, is limited to 1,000 roles and 262,144 UTF-8 bytes.
Oversized graph input fails explicitly before generation, rather than dropping
claims or fields. This bound is independent of the ordinary 4,000-character tool
excerpt budget. Source values are JSON-quoted and labeled as untrusted data.
Other tool formatting is unchanged.

## Explicit time contract

Dates are finite Unix seconds; timezone conversion belongs to the caller.

- `valid_at=t` requires `mode="audit"` and means `valid_from <= t < valid_to`.
  A missing start cannot support
  a dated answer and is excluded. A missing end is open for other statuses, but
  a superseded claim requires an explicit end. Distinct retained superseded
  records can therefore support past validity queries
- `occurred_from` is inclusive and `occurred_before` exclusive. Unknown
  `occurred_at` values are excluded when an occurred-time filter is supplied
- The excluded-unknown-time count applies to structural matches, counts each
  claim once, and is independent of other date bounds and the result limit
- `occurred_at` is a stored event/source timestamp. It is not a universal
  source-message-time or database-recorded-time guarantee
- No status or validity interval is inferred from creation/update timestamps.
  Audit mode retains statuses unless the caller filters them explicitly

This reads currently retained records with their supplied time annotations.
Same-ID upserts replace the old record. `known_at` deliberately raises an
unsupported-history error rather than pretending to reconstruct what the
system knew then. The separate Action History store is not queried.

## Attribution and disagreement

No confidence or recency winner is chosen. Overlapping contradictory claims
remain separate, source-attributed results. Truncated results cannot establish
that no other alternatives exist. The reader does not declare every multi-valued
relation a conflict: it has no functional-predicate or conflict-resolution model.

`origin` and `accepted_by_user` are currently free-form metadata, returned
unchanged. An assistant proposal is not promoted into a user decision, and
confidence is not proof of objective truth or adoption. Missing sources stay
missing; the tool does not invent source URLs or quotations.

No schema migration, new indexes, graph mutation, embedding architecture, new
HTTP endpoint, runtime deployment or production database changes are included.
`graph.query` is registered as a read-only built-in tool.


## Offline verification

Run the synthetic reader and privacy/continuation checks with isolated temporary
application data and outbound socket connections blocked:

```sh
poetry run python scripts/verify_graph_query_offline.py --repo . -q \
  backend/app/tests/test_graph_query.py \
  backend/app/tests/test_graph_query_privacy.py \
  backend/app/tests/test_graph_query_prompt.py
```

The same wrapper accepts explicit pytest paths for the existing graph, tool,
privacy, compaction and RAG regressions. It does not implicitly select the full
suite. These checks use synthetic sources and mocked model responses, not live
providers or production memory.
