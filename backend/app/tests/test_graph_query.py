"""Read-only retrieval contracts over synthetic graph data; no model calls."""

import hashlib
import sqlite3

import pytest
from app.services.graph_payload_service import apply_graph_payload
from app.tool_catalog import get_tool_catalog_entry
from app.tool_specs import BUILTIN_TOOL_SPECS
from app.tools import BUILTIN_TOOLS
from app.tools import graph as graph_tools
from app.utils import generate_signature, sanitize_args
from app.utils.graph_store import GraphStore
from app.utils.tool_args import normalize_tool_args


@pytest.fixture
def local_policy():
    from app.utils.privacy_policy import DataPolicy, data_policy_scope

    with data_policy_scope(DataPolicy(external=False, allow_remote_query=False)):
        yield


@pytest.fixture
def store(tmp_path):
    store = GraphStore(tmp_path / "synthetic.sqlite3")
    result = apply_graph_payload(
        store,
        graph_nodes=[
            {"node_id": name, "node_type": "synthetic", "canonical_name": name}
            for name in ("project", "red", "blue", "green", "other")
        ],
        graph_claims=[
            {
                "claim_id": claim_id,
                "predicate": "test_environment",
                "epistemic_status": status,
                "valid_from": start,
                "valid_to": end,
                "occurred_at": occurred,
                "source_kind": "synthetic",
                "source_ref": "synthetic:" + claim_id,
                "metadata": {"origin": "synthetic", "source_message_id": claim_id},
                "roles": [
                    {"role_name": "subject", "node_id": "project"},
                    {"role_name": "object", "node_id": value},
                ],
            }
            for claim_id, value, status, start, end, occurred in (
                ("old", "red", "superseded", 10, 20, 9),
                ("new", "blue", "asserted", 20, None, 19),
                ("overlap", "green", "asserted", 25, 30, 25),
            )
        ],
    )
    assert result["errors"] == []
    return store


def ids(result):
    return [c["claim_id"] for c in result["claims"]]


def test_complete_literal_roles_provenance_survive_reload(store):
    roles = [
        {"role_name": "subject", "node_id": "project"},
        {
            "role_name": "plan",
            "value": {"text": "after synthetic exam", "date": None},
            "metadata": {"qualifier": "not exact date"},
        },
        {"role_name": "count", "value": 0},
        {"role_name": "adopted", "value": False},
        {"role_name": "tags", "value": ["synthetic", "literal"]},
        {"role_name": "empty", "value": ""},
        {"role_name": "unknown", "value": None, "value_present": True},
        {"role_name": "participant", "node_id": "blue"},
        {"role_name": "participant", "node_id": "green"},
    ]
    saved = store.upsert_claim(
        claim_id="plan",
        predicate="testing_plan",
        roles=roles,
        source_kind="synthetic",
        source_ref="synthetic:plan",
        epistemic_status="scheduled",
        metadata={
            "origin": "synthetic",
            "source_date_utc": "2026-10-02",
            "accepted_by_user": False,
        },
    )
    # Ordinary upsert skips a None-only role. Restore/sync can retain explicit
    # JSON null, so seed that existing storage shape without changing ingestion.
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO graph_claim_roles "
            "(claim_id, role_name, ordinal, node_id, value_json, metadata_json) "
            "VALUES (?, ?, ?, NULL, ?, ?)",
            ("plan", "unknown", 6, "null", "{}"),
        )
    saved = store.get_claim("plan")
    reopened = GraphStore(store.path)
    result = reopened.query_claims(mode="audit", predicate="testing_plan")
    assert result["claims"] == [saved]
    assert len(result["claims"][0]["roles"]) == 9
    literals = [r for r in result["claims"][0]["roles"] if r["value_present"]]
    assert len(literals) == 6
    assert any(r["value"] is None for r in literals)
    assert {n["node_id"] for n in result["nodes"]} == {"project", "blue", "green"}
    assert saved["source_ref"] == "synthetic:plan"
    assert saved["metadata"]["accepted_by_user"] is False
    assert saved["epistemic_status"] == "scheduled"


def test_relation_direction_and_conjunctive_role_filters(store):
    store.upsert_claim(
        claim_id="reverse",
        predicate="test_environment",
        roles=[
            {"role_name": "subject", "node_id": "other"},
            {"role_name": "object", "node_id": "project"},
        ],
    )
    assert set(ids(store.query_claims(mode="audit", node_id="project"))) == {
        "old",
        "new",
        "overlap",
        "reverse",
    }
    assert ids(
        store.query_claims(
            mode="audit",
            predicate="test_environment",
            role_nodes={"subject": "project", "object": "blue"},
        )
    ) == ["new"]
    assert ids(store.query_claims(mode="audit", role_nodes={"subject": "blue"})) == []
    assert ids(store.query_claims(mode="audit", node_id="missing")) == []
    assert ids(store.query_claims(mode="audit", predicate="not_present")) == []


@pytest.mark.parametrize(
    "at,expected",
    [
        (10, ["old"]),
        (19, ["old"]),
        (20, ["new"]),
        (25, ["new", "overlap"]),
        (30, ["new"]),
    ],
)
def test_validity_half_open_boundary_preserves_superseded_history(store, at, expected):
    assert ids(store.query_claims(mode="audit", valid_at=at)) == expected


def test_occurred_range_is_distinct_inclusive_exclusive_time(store):
    assert ids(
        store.query_claims(mode="audit", occurred_from=19, occurred_before=25)
    ) == ["new"]
    assert ids(store.query_claims(mode="audit", occurred_before=19)) == ["old"]
    assert ids(store.query_claims(mode="audit", valid_at=20, occurred_before=19)) == []


def test_unknown_times_are_excluded_only_by_dated_queries(store):
    for claim_id, status, start, end in (
        ("unknown_start", "asserted", None, None),
        ("unknown_end", "superseded", 10, None),
    ):
        store.upsert_claim(
            claim_id=claim_id,
            predicate="test_environment",
            epistemic_status=status,
            valid_from=start,
            valid_to=end,
            roles=[
                {"role_name": "subject", "node_id": "project"},
                {"role_name": "object", "value": "unknown time"},
            ],
        )
    assert (
        len(
            ids(
                store.query_claims(
                    mode="audit",
                )
            )
        )
        == 5
    )
    result = store.query_claims(mode="audit", valid_at=20)
    assert ids(result) == ["new"]
    assert result["metadata"]["excluded_unknown_time_count"] == 2
    combined = store.query_claims(mode="audit", valid_at=20, occurred_before=50)
    assert (
        combined["metadata"]["excluded_unknown_time_count"] == 2
    )  # union, not double counted


def test_conflicting_values_are_returned_without_winner(store):
    result = store.query_claims(mode="audit", valid_at=26)
    assert ids(result) == ["new", "overlap"]
    assert (
        result["metadata"]["conflict_resolution"]
        == "none; all matching claims retained"
    )
    assert all("source_ref" in c and "metadata" in c for c in result["claims"])
    limited = store.query_claims(mode="audit", valid_at=26, limit=1)
    assert limited["metadata"]["truncated"] is True
    assert limited["metadata"]["matched_claim_count"] == 2


def test_limits_apply_after_filters_and_stable_order(store):
    for i in range(110):
        store.upsert_claim(
            claim_id=f"a{i:03}",
            predicate="noise",
            roles=[
                {"role_name": "subject", "node_id": "project"},
                {"role_name": "object", "value": i},
            ],
        )
    result = store.query_claims(
        mode="audit",
        predicate="test_environment",
        role_nodes={"subject": "project"},
        limit=2,
    )
    assert ids(result) == ["new", "old"]
    assert result["metadata"]["matched_claim_count"] == 3
    assert result["metadata"]["truncated"] is True
    maximum = store.query_claims(mode="audit", limit=100)
    assert len(maximum["claims"]) == 100
    assert maximum["metadata"]["matched_claim_count"] == 113
    assert maximum["metadata"]["truncated"] is True


def test_proposal_attribution_and_all_epistemic_statuses_are_preserved(store):
    store.upsert_claim(
        claim_id="proposal",
        predicate="proposes",
        epistemic_status="hypothesized",
        metadata={"origin": "assistant_proposal", "accepted_by_user": False},
        roles=[
            {"role_name": "subject", "node_id": "project"},
            {"role_name": "object", "value": "synthetic idea"},
        ],
    )
    result = store.query_claims(mode="audit", epistemic_status="hypothesized")
    assert ids(result) == ["proposal"]
    assert result["claims"][0]["metadata"] == {
        "origin": "assistant_proposal",
        "accepted_by_user": False,
    }
    assert ids(store.query_claims(mode="audit", epistemic_status="superseded")) == [
        "old"
    ]


def test_same_id_overwrite_is_not_reconstructed_as_knowledge_history(store):
    store.upsert_claim(
        claim_id="new",
        predicate="test_environment",
        roles=[
            {"role_name": "subject", "node_id": "project"},
            {"role_name": "object", "value": "overwritten"},
        ],
    )
    result = store.query_claims(
        mode="audit", role_nodes={"subject": "project"}, predicate="test_environment"
    )
    assert len(result["claims"]) == 3
    assert (
        next(c for c in result["claims"] if c["claim_id"] == "new")["roles"][1]["value"]
        == "overwritten"
    )
    assert result["metadata"]["knowledge_as_of_supported"] is False
    with pytest.raises(ValueError, match="known_at is unsupported.*overwrites"):
        store.query_claims(mode="audit", known_at=19)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.5},
        {"valid_at": float("nan")},
        {"occurred_before": float("inf")},
        {"occurred_from": True},
        {"valid_at": "yesterday"},
        {"occurred_from": 20, "occurred_before": 20},
        {"node_id": ""},
        {"predicate": 7},
        {"role_nodes": []},
        {"role_nodes": {"subject": ""}},
        {"role_nodes": {str(i): "project" for i in range(9)}},
        {"epistemic_status": "probably"},
    ],
)
def test_invalid_queries_have_clear_errors(store, kwargs):
    with pytest.raises(ValueError):
        store.query_claims(mode="audit", **kwargs)


def test_parameterized_strings_cannot_expand_query(store):
    assert (
        ids(store.query_claims(mode="audit", predicate="test_environment' OR 1=1 --"))
        == []
    )
    assert (
        ids(
            store.query_claims(
                mode="audit", role_nodes={"subject' OR 1=1 --": "project"}
            )
        )
        == []
    )


def test_tool_signature_defaults_registration_and_unavailable_error(
    store, monkeypatch, local_policy
):
    monkeypatch.setattr(graph_tools, "_GRAPH_STORE", store)
    assert BUILTIN_TOOLS["graph.query"] is graph_tools.query_graph
    args = normalize_tool_args(
        "graph.query", {"mode": "audit", "predicate": "test_environment"}
    )
    signed = generate_signature("synthetic-user", "graph.query", args)
    result = graph_tools.query_graph(user="synthetic-user", signature=signed, **args)
    assert ids(result) == ["new", "old", "overlap"]
    empty_sig = generate_signature("synthetic-user", "graph.query", {})
    assert (
        graph_tools.query_graph(user="synthetic-user", signature=empty_sig)["metadata"][
            "limit"
        ]
        == 50
    )
    with pytest.raises(PermissionError):
        graph_tools.query_graph(
            user="synthetic-user", signature=signed, predicate="noise", limit=50
        )
    with pytest.raises(ValueError, match="known_at is unsupported"):
        normalize_tool_args("graph.query", {"known_at": 19})
    history_args = {"known_at": None}
    with pytest.raises(ValueError, match="known_at is unsupported"):
        graph_tools.query_graph(
            user="synthetic-user",
            signature=generate_signature("synthetic-user", "graph.query", history_args),
            **history_args,
        )
    monkeypatch.setattr(graph_tools, "_GRAPH_STORE", None)
    with pytest.raises(RuntimeError, match="graph store not available"):
        graph_tools.query_graph(user="synthetic-user", signature=empty_sig)


def test_query_does_not_write_database_and_catalog_declares_read_only(store):
    before = hashlib.sha256(store.path.read_bytes()).hexdigest()
    trace = []
    connect = store._connect

    def traced_connect():
        conn = connect()
        conn.set_trace_callback(trace.append)
        return conn

    store._connect = traced_connect
    store.query_claims(mode="audit", valid_at=26, limit=1)
    assert hashlib.sha256(store.path.read_bytes()).hexdigest() == before
    assert not any(
        sql.lstrip().split()[0].upper()
        in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE"}
        for sql in trace
    )
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    entry = get_tool_catalog_entry("graph.query")
    assert entry["persistence"]["writes_state"] is False
    assert entry["runtime"]["network"] is False
    assert entry["limits"]["max_limit"] == 100
    assert (
        BUILTIN_TOOL_SPECS["graph.query"]["parameters"]["additionalProperties"] is False
    )


def test_literal_only_claim_and_missing_source_are_not_dropped_or_invented(store):
    stored = store.upsert_claim(
        claim_id="literal_only",
        predicate="synthetic_observation",
        roles=[{"role_name": "measurement", "value": 0.0}],
        confidence=0.0,
        epistemic_status="observed",
    )
    result = store.query_claims(mode="audit", predicate="synthetic_observation")
    assert result["claims"] == [stored]
    assert result["nodes"] == []
    assert stored["source_kind"] is None and stored["source_ref"] is None
    assert stored["metadata"] == {}
    assert stored["confidence"] == 0.0


def test_query_uses_one_snapshot_for_claims_roles_nodes_and_counts(store, monkeypatch):
    # WAL allows a second writer while the query holds its read snapshot.
    with store._connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
    original_connect = store._connect
    changed = False

    def load_with_concurrent_change(row, *, role_rows=None):
        nonlocal changed
        if not changed:
            with original_connect() as writer:
                writer.execute(
                    "UPDATE graph_nodes SET canonical_name = 'changed' WHERE node_id = 'blue'"
                )
                writer.execute(
                    "UPDATE graph_claim_roles SET value_json = '\"changed\"', node_id = NULL WHERE claim_id = 'new' AND role_name = 'object'"
                )
            changed = True
        return original_load(row, role_rows=role_rows)

    original_load = store._load_claim
    monkeypatch.setattr(store, "_load_claim", load_with_concurrent_change)
    result = store.query_claims(mode="audit", predicate="test_environment")
    assert result["metadata"]["matched_claim_count"] == 3
    new = next(c for c in result["claims"] if c["claim_id"] == "new")
    assert (
        next(r for r in new["roles"] if r["role_name"] == "object")["node_id"] == "blue"
    )
    assert (
        next(n for n in result["nodes"] if n["node_id"] == "blue")["canonical_name"]
        == "blue"
    )
    assert store.get_node("blue")["canonical_name"] == "changed"


@pytest.mark.parametrize(
    "raw",
    [
        {"valid_at": True},
        {"occurred_from": False},
        {"occurred_before": float("nan")},
        {"limit": False},
        {"limit": 101},
        {"limit": 1.5},
        {"known_at": None},
        {"node_id": "x" * 513},
        {"role_nodes": {"x" * 513: "project"}},
    ],
)
def test_normalizer_rejects_invalid_graph_query_before_coercion(raw):
    with pytest.raises(ValueError):
        sanitize_args(normalize_tool_args("graph.query", raw))


def test_signed_normalized_sanitized_query_preserves_boundaries(
    store, monkeypatch, local_policy
):
    monkeypatch.setattr(graph_tools, "_GRAPH_STORE", store)
    raw = {
        "mode": "audit",
        "role_nodes": {"subject": "project"},
        "valid_at": 20,
        "occurred_before": 25,
        "limit": 1,
    }
    args = sanitize_args(normalize_tool_args("graph.query", raw))
    result = graph_tools.query_graph(
        user="synthetic-user",
        signature=generate_signature("synthetic-user", "graph.query", args),
        **args,
    )
    assert ids(result) == ["new"]
    assert result["metadata"]["truncated"] is False


@pytest.mark.parametrize(
    "large_field", ["literal", "claim_metadata", "node_attributes"]
)
def test_oversized_complete_response_fails_without_clipping(store, large_field):
    large = "synthetic-only-" * 80000
    roles = [
        {"role_name": "subject", "node_id": "project"},
        {
            "role_name": "object",
            "value": large if large_field == "literal" else "small",
        },
    ]
    store.upsert_claim(
        claim_id="large",
        predicate="large",
        roles=roles,
        metadata={"large": large} if large_field == "claim_metadata" else {},
    )
    if large_field == "node_attributes":
        store.upsert_node(
            node_id="project",
            node_kind="entity",
            node_type="synthetic",
            attributes={"large": large},
        )
    with pytest.raises(
        ValueError, match="result too large.*no incomplete claims returned"
    ):
        store.query_claims(mode="audit", predicate="large", limit=1)
    assert len(store.get_claim("large")["roles"]) == 2


def test_excessive_roles_fail_without_incomplete_claim(store):
    store.upsert_claim(
        claim_id="many_roles",
        predicate="many_roles",
        roles=[{"role_name": "entry", "ordinal": i, "value": i} for i in range(1001)],
    )
    with pytest.raises(ValueError, match="more than 1000 roles"):
        store.query_claims(mode="audit", predicate="many_roles", limit=1)
    assert len(store.get_claim("many_roles")["roles"]) == 1001


def test_local_signed_tool_response_is_within_declared_utf8_budget(
    store, monkeypatch, local_policy
):
    import json

    monkeypatch.setattr(graph_tools, "_GRAPH_STORE", store)
    args = sanitize_args(normalize_tool_args("graph.query", {}))
    result = graph_tools.query_graph(
        user="synthetic-user",
        signature=generate_signature("synthetic-user", "graph.query", args),
        **args,
    )
    assert (
        len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        <= result["metadata"]["max_response_bytes"]
    )
    assert (
        sum(len(c["roles"]) for c in result["claims"])
        <= result["metadata"]["max_roles"]
    )


def test_external_policy_denies_before_read_and_after_signature(store, monkeypatch):
    from app.utils.privacy_policy import DataPolicy, data_policy_scope

    monkeypatch.setattr(graph_tools, "_GRAPH_STORE", store)
    calls = []

    def forbidden_query(**kwargs):
        calls.append(kwargs)
        raise AssertionError("External policy must deny before graph reads")

    monkeypatch.setattr(store, "query_claims", forbidden_query)
    args = sanitize_args(normalize_tool_args("graph.query", {}))
    signed = generate_signature("synthetic-user", "graph.query", args)
    with data_policy_scope(DataPolicy(external=True, allow_remote_query=True)):
        with pytest.raises(PermissionError, match="Invalid signature"):
            graph_tools.query_graph(user="synthetic-user", signature="invalid", **args)
        with pytest.raises(PermissionError, match="local-only"):
            graph_tools.query_graph(user="synthetic-user", signature=signed, **args)
    assert calls == []


def test_memory_manager_denies_external_graph_but_allows_local_with_no_journal(
    store, monkeypatch
):
    from app.base_services import MemoryManager

    manager = MemoryManager({"memory_store_path": str(store.path)})
    manager.register_tool("graph.query", graph_tools.query_graph)
    monkeypatch.setattr(graph_tools, "_GRAPH_STORE", manager._graph_store)
    secret = "SYNTHETIC_ONLY_PRIVATE_GRAPH_VALUE"
    manager._graph_store.upsert_claim(
        claim_id="private",
        predicate="private",
        metadata={"sensitivity": "secret", "memory_key": "synthetic-memory"},
        roles=[
            {"role_name": "subject", "node_id": "project"},
            {"role_name": "object", "value": secret},
        ],
    )

    class NoJournal:
        def prepare_tool_action(self, *args, **kwargs):
            raise AssertionError("Read-only query must not journal an action")

    manager.set_action_history_service(NoJournal())
    args = sanitize_args(normalize_tool_args("graph.query", {"predicate": "private"}))
    signature = generate_signature("synthetic-user", "graph.query", args)
    with pytest.raises(PermissionError, match="local-only"):
        manager.invoke_tool(
            "graph.query",
            user="synthetic-user",
            signature=signature,
            _action_context={"mode": "api", "endpoint": "https://example.invalid/v1"},
            **args,
        )
    result = manager.invoke_tool(
        "graph.query",
        user="synthetic-user",
        signature=signature,
        _action_context={"mode": "local", "endpoint": "http://127.0.0.1:1234/v1"},
        **args,
    )
    assert (
        next(
            r["value"]
            for r in result["claims"][0]["roles"]
            if r["role_name"] == "object"
        )
        == secret
    )


@pytest.mark.parametrize(
    "status",
    [
        "rejected",
        "inactive",
        "cancelled",
        "superseded",
        "expired",
        "archived",
        "deleted",
        "pending",
        "unknown",
    ],
)
def test_current_excludes_non_active_status_before_limits(store, status, monkeypatch):
    monkeypatch.setattr("app.utils.graph_store.time.time", lambda: 26.0)
    for index in range(101):
        store.upsert_claim(
            claim_id=f"a-withdrawn-{index:03}",
            predicate="lifecycle",
            status=status,
            roles=[{"role_name": "object", "value": "withdrawn synthetic value"}],
            source_ref=f"synthetic:{index}",
        )
    current = store.upsert_claim(
        claim_id="z-current",
        predicate="lifecycle",
        roles=[{"role_name": "object", "value": "eligible synthetic value"}],
        source_ref="synthetic:current",
    )
    result = store.query_claims(predicate="lifecycle", limit=1)
    assert result["claims"] == [current]
    assert result["metadata"]["matched_claim_count"] == 1
    assert result["metadata"]["truncated"] is False
    assert result["metadata"]["mode"] == "current"
    assert result["metadata"]["current_at"] == 26.0
    audit = store.query_claims(mode="audit", predicate="lifecycle", limit=100)
    assert audit["metadata"]["matched_claim_count"] == 102
    assert audit["metadata"]["truncated"] is True
    assert audit["claims"][0]["status"] == status
    assert audit["claims"][0]["source_ref"] == "synthetic:0"


@pytest.mark.parametrize("epistemic_status", ["cancelled", "superseded"])
def test_current_excludes_withdrawn_epistemic_status_even_if_active(
    store, epistemic_status
):
    store.upsert_claim(
        claim_id="withdrawn",
        predicate="lifecycle",
        status="active",
        epistemic_status=epistemic_status,
        roles=[{"role_name": "object", "value": "retained for audit"}],
    )
    assert ids(store.query_claims(predicate="lifecycle")) == []
    assert (
        ids(
            store.query_claims(predicate="lifecycle", epistemic_status=epistemic_status)
        )
        == []
    )
    assert ids(store.query_claims(mode="audit", predicate="lifecycle")) == ["withdrawn"]


@pytest.mark.parametrize(
    "start,end,eligible",
    [
        (None, None, True),
        (25, 27, True),
        (26, 27, True),
        (25, 26, False),
        (25, 25.5, False),
        (27, None, False),
        (None, 26, False),
        (27, 25, False),
    ],
)
def test_current_uses_present_half_open_validity_before_limit(
    store, monkeypatch, start, end, eligible
):
    monkeypatch.setattr("app.utils.graph_store.time.time", lambda: 26.0)
    store.upsert_claim(
        claim_id="a-timed",
        predicate="current_time",
        valid_from=start,
        valid_to=end,
        roles=[{"role_name": "object", "value": "timed synthetic value"}],
    )
    store.upsert_claim(
        claim_id="z-unbounded",
        predicate="current_time",
        roles=[{"role_name": "object", "value": "unbounded synthetic value"}],
    )
    result = store.query_claims(predicate="current_time", limit=1)
    assert ids(result) == ["a-timed" if eligible else "z-unbounded"]
    assert result["metadata"]["matched_claim_count"] == (2 if eligible else 1)
    assert result["metadata"]["truncated"] is eligible
    assert ids(store.query_claims(mode="audit", predicate="current_time")) == [
        "a-timed",
        "z-unbounded",
    ]


def test_current_preserves_disagreement_and_proposals_without_claiming_adoption(
    store, monkeypatch
):
    monkeypatch.setattr("app.utils.graph_store.time.time", lambda: 26.0)
    store.upsert_claim(
        claim_id="proposal",
        predicate="test_environment",
        epistemic_status="hypothesized",
        source_kind="assistant",
        source_ref="synthetic:assistant-proposal",
        metadata={"origin": "assistant_proposal", "accepted_by_user": False},
        roles=[{"role_name": "object", "value": "a possible option"}],
    )
    result = store.query_claims(predicate="test_environment")
    assert ids(result) == ["new", "overlap", "proposal"]
    assert result["claims"][-1]["epistemic_status"] == "hypothesized"
    assert result["claims"][-1]["metadata"]["accepted_by_user"] is False
    assert result["claims"][-1]["source_ref"] == "synthetic:assistant-proposal"
    assert (
        "not verified facts or user decisions"
        in result["metadata"]["current_claims_contract"]
    )
    assert (
        result["metadata"]["conflict_resolution"]
        == "none; all matching claims retained"
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"mode": None},
        {"mode": True},
        {"mode": "history"},
        {"mode": []},
        {"valid_at": 26},
        {"status": "rejected"},
        {"unknown_filter": 1},
    ],
)
def test_query_mode_and_unknown_filters_fail_before_normalization(raw):
    with pytest.raises(ValueError):
        normalize_tool_args("graph.query", raw)


@pytest.mark.parametrize("mode", ["current", "audit"])
def test_missing_destination_policy_fails_before_store_access(monkeypatch, mode):
    def forbidden_store():
        raise AssertionError(
            "Store must not be accessed without local destination evidence"
        )

    monkeypatch.setattr(graph_tools, "_store", forbidden_store)
    monkeypatch.setattr(graph_tools.privacy_policy, "current_data_policy", lambda: None)
    args = normalize_tool_args("graph.query", {"mode": mode})
    with pytest.raises(PermissionError, match="explicit local"):
        graph_tools.query_graph(
            user="synthetic-user",
            signature=generate_signature("synthetic-user", "graph.query", args),
            **args,
        )


@pytest.mark.parametrize("field", ["valid_at", "occurred_from", "occurred_before"])
def test_finite_large_numeric_dates_bind_as_real_and_overflow_fails_clearly(
    store, field
):
    store.query_claims(mode="audit", **{field: 10**100})
    with pytest.raises(ValueError, match="finite Unix seconds"):
        store.query_claims(mode="audit", **{field: 10**1000})


def test_audit_validity_never_guesses_superseded_lifecycle_end(store):
    store.upsert_claim(
        claim_id="lifecycle_superseded",
        predicate="lifecycle",
        status=" Superseded ",
        valid_from=10,
        roles=[{"role_name": "value", "value": "retained old value"}],
    )
    assert ids(store.query_claims(mode="audit", predicate="lifecycle")) == [
        "lifecycle_superseded"
    ]
    result = store.query_claims(mode="audit", predicate="lifecycle", valid_at=15)
    assert result["claims"] == []
    assert result["metadata"]["excluded_unknown_time_count"] == 1


@pytest.mark.parametrize("external", [None, 0, "", "local", True])
def test_malformed_policy_is_not_explicit_local_evidence(monkeypatch, external):
    from app.utils.privacy_policy import DataPolicy

    def forbidden_store():
        raise AssertionError("Malformed policies must fail before reading")

    monkeypatch.setattr(graph_tools, "_store", forbidden_store)
    monkeypatch.setattr(
        graph_tools.privacy_policy,
        "current_data_policy",
        lambda: DataPolicy(external=external, allow_remote_query=False),
    )
    with pytest.raises(PermissionError, match="explicit local"):
        graph_tools.query_graph(
            user="synthetic-user",
            signature=generate_signature("synthetic-user", "graph.query", {}),
        )
