from app.base_services import MemoryManager
from app.services import privacy_filter_service as privacy_filter
from app.services.rag_service import RAGService
from app.tools import local_files
from app.tools import memory as memory_tools
from app.tools import routing
from app.utils import generate_signature


def _enable_fake_filter(monkeypatch, label="private_email", score=0.98):
    monkeypatch.setattr(
        privacy_filter.user_settings,
        "load_settings",
        lambda: {"privacy_filter_mode": "auto"},
    )

    def fake_classifier(_model):
        return lambda _text, aggregation_strategy="simple": [
            {"entity_group": label, "score": score}
        ]

    monkeypatch.setattr(privacy_filter, "_get_classifier", fake_classifier)


def test_remember_auto_sets_sensitivity_and_reports_notice(monkeypatch):
    _enable_fake_filter(monkeypatch)
    manager = MemoryManager({})
    memory_tools.set_manager(manager)

    args = {"key": "contact", "value": "email me at person@example.com"}
    sig = generate_signature("bob", "remember", args)

    result = memory_tools.remember(user="bob", signature=sig, **args)

    assert result.startswith("ok (privacy filter set sensitivity to protected")
    item = manager.get_item("contact", touch=False)
    assert item["sensitivity"] == "protected"
    assert item["sensitivity_source"] == "privacy_filter"
    assert item["privacy_filter_detected_labels"] == "private_email"


def test_remember_keeps_user_sensitivity_when_filter_always_checks(monkeypatch):
    monkeypatch.setattr(
        privacy_filter.user_settings,
        "load_settings",
        lambda: {"privacy_filter_mode": "always"},
    )

    def fake_classifier(_model):
        return lambda _text, aggregation_strategy="simple": [
            {"entity_group": "secret", "score": 0.99}
        ]

    monkeypatch.setattr(privacy_filter, "_get_classifier", fake_classifier)
    manager = MemoryManager({})
    memory_tools.set_manager(manager)

    args = {
        "key": "manual",
        "value": "keep the manual label",
        "sensitivity": "personal",
    }
    sig = generate_signature("bob", "remember", args)

    result = memory_tools.remember(user="bob", signature=sig, **args)

    assert "kept user sensitivity personal" in result
    item = manager.get_item("manual", touch=False)
    assert item["sensitivity"] == "personal"
    assert item["sensitivity_source"] == "user"
    assert item["privacy_filter_suggested_sensitivity"] == "secret"


def test_write_file_reports_privacy_filter_choice(monkeypatch, tmp_path):
    _enable_fake_filter(monkeypatch, label="private_phone")
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path))

    args = {"path": "note.txt", "content": "Call 555-123-4567"}
    sig = generate_signature("bob", "write_file", args)

    result = local_files.write_file(user="bob", signature=sig, **args)

    assert result.startswith("written (privacy filter set sensitivity to protected")
    assert (tmp_path / "workspace" / "note.txt").read_text() == "Call 555-123-4567"


def test_rag_ingest_adds_privacy_metadata(monkeypatch, tmp_path):
    _enable_fake_filter(monkeypatch, label="secret")
    service = RAGService(
        backend="chroma",
        persist_dir=str(tmp_path / "chroma"),
        sqlite_path=str(tmp_path / "memory.sqlite3"),
        enable_canonical_store=False,
    )

    doc_id = service.ingest_text(
        "api token: abc123",
        {"source": "workspace/secrets.txt", "kind": "document"},
    )
    trace = service.trace(doc_id)

    assert trace is not None
    metadata = trace["metadata"]
    assert metadata["sensitivity"] == "secret"
    assert metadata["sensitivity_source"] == "privacy_filter"
    assert metadata["privacy_filter_detected_labels"] == "secret"


def test_conversation_privacy_filter_updates_metadata(monkeypatch, tmp_path):
    _enable_fake_filter(monkeypatch)
    from app import routes

    monkeypatch.setattr(routes.conversation_store, "CONV_DIR", tmp_path)

    updates = routes._apply_conversation_privacy_filter(
        "privacy-chat",
        [{"role": "user", "content": "email me at person@example.com"}],
    )

    assert updates["sensitivity"] == "protected"
    assert updates["privacy_mode"] == "protected"
    metadata = routes.conversation_store.get_metadata("privacy-chat")
    assert metadata["sensitivity_source"] == "privacy_filter"
    assert metadata["privacy_filter_detected_labels"] == "private_email"


def test_private_message_route_proposes_local_model(monkeypatch):
    from app import routes

    def fake_classifier(_model):
        return lambda _text, aggregation_strategy="simple": [
            {"entity_group": "secret", "score": 0.99}
        ]

    monkeypatch.setattr(privacy_filter, "_get_classifier", fake_classifier)

    result = routes._privacy_route_check_for_message(
        "api token: abc123",
        settings_payload={
            "privacy_filter_mode": "always",
            "privacy_filter_route_private_mode": "ask",
            "privacy_filter_route_min_sensitivity": "protected",
        },
        mode_used="api",
        requested_model="gpt-5.4",
        config_payload={"transformer_model": "local-private-model"},
    )

    assert result is not None
    tool = result["tool"]
    assert tool["name"] == "route_to_local_model"
    assert tool["args"]["target_mode"] == "local"
    assert tool["args"]["target_model"] == "local-private-model"
    assert tool["args"]["source_mode"] == "api"
    assert tool["args"]["source_model"] == "gpt-5.4"
    assert tool["args"]["sensitivity"] == "secret"
    assert tool["args"]["labels"] == "secret"
    assert result["metadata"]["privacy_route_status"] == "proposed"


def test_private_message_route_skips_local_mode(monkeypatch):
    from app import routes

    def fail_classifier(_model):
        raise AssertionError("local mode should not run route classifier")

    monkeypatch.setattr(privacy_filter, "_get_classifier", fail_classifier)

    assert (
        routes._privacy_route_check_for_message(
            "api token: abc123",
            settings_payload={
                "privacy_filter_mode": "always",
                "privacy_filter_route_private_mode": "ask",
            },
            mode_used="local",
            requested_model="local-private-model",
            config_payload={"transformer_model": "local-private-model"},
        )
        is None
    )


def test_private_message_route_detector_off_skips_even_when_route_ask(monkeypatch):
    from app import routes

    def fail_classifier(_model):
        raise AssertionError("disabled privacy detector should not load classifier")

    monkeypatch.setattr(privacy_filter, "_get_classifier", fail_classifier)

    assert (
        routes._privacy_route_check_for_message(
            "api token: abc123",
            settings_payload={
                "privacy_filter_mode": "off",
                "privacy_filter_route_private_mode": "ask",
            },
            mode_used="api",
            requested_model="gpt-5.4",
            config_payload={"transformer_model": "local-private-model"},
        )
        is None
    )


def test_route_to_local_model_accepts_signed_payload():
    args = {
        "target_mode": "local",
        "target_model": "local-private-model",
        "reason": "Privacy filter detected protected text.",
        "sensitivity": "protected",
        "labels": "private_email",
        "source_mode": "api",
        "source_model": "gpt-5.4",
    }
    sig = generate_signature("bob", "route_to_local_model", args)

    result = routing.route_to_local_model(user="bob", signature=sig, **args)

    assert result["status"] == "accepted"
    assert result["route"]["mode"] == "local"
    assert result["route"]["model"] == "local-private-model"
