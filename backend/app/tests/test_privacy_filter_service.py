from app.services import privacy_filter_service as privacy_filter


def test_privacy_filter_auto_escalates_to_secret(monkeypatch):
    observed = {}

    def fake_classifier(_model):
        observed["model"] = _model

        def classify(_text, aggregation_strategy="simple"):
            assert aggregation_strategy == "simple"
            return [{"entity_group": "secret", "score": 0.99, "start": 0, "end": 8}]

        return classify

    monkeypatch.setattr(privacy_filter, "_get_classifier", fake_classifier)

    decision = privacy_filter.decide_sensitivity(
        "token=abc",
        settings={"privacy_filter_mode": "auto"},
    )

    assert decision.status == "matched"
    assert decision.action == "applied"
    assert decision.applied_sensitivity == "secret"
    assert decision.applied_source == "privacy_filter"
    assert decision.labels == ["secret"]
    assert observed["model"] == "openai/privacy-filter"


def test_privacy_filter_resolves_download_alias(monkeypatch):
    observed = {}

    def fake_classifier(model):
        observed["model"] = model
        return lambda _text, aggregation_strategy="simple": []

    monkeypatch.setattr(privacy_filter, "_get_classifier", fake_classifier)

    privacy_filter.decide_sensitivity(
        "plain text",
        settings={
            "privacy_filter_mode": "always",
            "privacy_filter_model": "privacy-filter",
        },
    )

    assert observed["model"] == "openai/privacy-filter"


def test_privacy_filter_respects_user_sensitivity_in_always_mode(monkeypatch):
    def fake_classifier(_model):
        return lambda _text, aggregation_strategy="simple": [
            {"entity_group": "private_email", "score": 0.98}
        ]

    monkeypatch.setattr(privacy_filter, "_get_classifier", fake_classifier)

    decision = privacy_filter.decide_sensitivity(
        "email me at person@example.com",
        explicit_sensitivity="personal",
        settings={"privacy_filter_mode": "always"},
    )

    assert decision.status == "matched"
    assert decision.suggested_sensitivity == "protected"
    assert decision.action == "kept_user"
    assert decision.applied_sensitivity == "personal"
    assert "kept user sensitivity personal" in privacy_filter.notice(decision)


def test_privacy_filter_off_does_not_load_model(monkeypatch):
    def fail_classifier(_model):
        raise AssertionError("classifier should not load when disabled")

    monkeypatch.setattr(privacy_filter, "_get_classifier", fail_classifier)

    decision = privacy_filter.decide_sensitivity(
        "person@example.com",
        settings={"privacy_filter_mode": "off"},
    )

    assert decision.status == "disabled"
    assert decision.action == "not_checked"
    assert privacy_filter.metadata_updates(decision) == {}


def test_apply_to_metadata_marks_auto_source(monkeypatch):
    def fake_classifier(_model):
        return lambda _text, aggregation_strategy="simple": [
            {"entity_group": "private_phone", "score": 0.95}
        ]

    monkeypatch.setattr(privacy_filter, "_get_classifier", fake_classifier)

    metadata = privacy_filter.apply_to_metadata(
        "call 555-123-4567",
        {"source": "workspace/note.txt"},
        settings={"privacy_filter_mode": "auto"},
    )

    assert metadata["sensitivity"] == "protected"
    assert metadata["sensitivity_source"] == "privacy_filter"
    assert metadata["privacy_filter_suggested_sensitivity"] == "protected"
    assert metadata["privacy_filter_detected_labels"] == "private_phone"
