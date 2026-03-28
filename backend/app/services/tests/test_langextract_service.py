from app.services.langextract_service import (LangExtractService,  # noqa: E501
                                              data, lx)


def test_from_text_wraps_extract(monkeypatch):
    doc = data.AnnotatedDocument(
        extractions=[
            data.Extraction(
                "message",
                "hi",
                attributes={"speaker": "A"},
            )
        ]
    )
    called: dict[str, object] = {}

    def fake_extract(text, prompt_description=None, examples=None):
        called["text"] = text
        called["prompt"] = prompt_description
        called["examples"] = examples
        return doc

    monkeypatch.setattr(lx, "extract", fake_extract)

    svc = LangExtractService("prompt", [])
    result = svc.from_text("A: hi")
    assert result == [
        {"class": "message", "text": "hi", "attributes": {"speaker": "A"}}
    ]
    assert called["text"] == "A: hi"
    assert called["prompt"] == "prompt"
    assert called["examples"] == []


def test_from_conversation_joins_messages(monkeypatch):
    doc = data.AnnotatedDocument(extractions=[])
    captured: dict[str, object] = {}

    def fake_extract(text, prompt_description=None, examples=None):
        captured["text"] = text
        return doc

    monkeypatch.setattr(lx, "extract", fake_extract)

    svc = LangExtractService("prompt", [])
    messages = [
        {"speaker": "A", "text": "hello"},
        {"speaker": "B", "text": "hi"},
    ]
    result = svc.from_conversation(messages)
    assert result == []
    assert captured["text"] == "A: hello\nB: hi"
