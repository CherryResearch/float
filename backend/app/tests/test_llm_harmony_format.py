from app.base_services import LLMService


def test_generate_filters_harmony_response_format(monkeypatch):
    import app.base_services as base_services

    payload = {}

    class DummyResp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

        def raise_for_status(self):
            pass

    def fake_post(url, **kwargs):
        payload.update(kwargs.get("json") or {})
        return DummyResp()

    monkeypatch.setattr(base_services.http_session, "post", fake_post)

    cfg = {
        "api_key": "x",
        "api_model": "m",
        "api_url": "http://example/v1/chat/completions",
    }
    svc = LLMService(config=cfg)
    res = svc.generate("hi", response_format="harmony")
    assert res["text"] == "ok"
    assert "response_format" not in payload


def test_generate_forwards_json_object_response_format(monkeypatch):
    import app.base_services as base_services

    payload = {}

    class DummyResp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

        def raise_for_status(self):
            pass

    def fake_post(url, **kwargs):
        payload.update(kwargs.get("json") or {})
        return DummyResp()

    monkeypatch.setattr(base_services.http_session, "post", fake_post)

    cfg = {
        "api_key": "x",
        "api_model": "m",
        "api_url": "http://example/v1/chat/completions",
    }
    svc = LLMService(config=cfg)
    res = svc.generate("hi", response_format="json_object")
    assert res["text"] == "ok"
    assert payload["response_format"] == {"type": "json_object"}


def test_generate_filters_harmony_response_format_in_server_mode(monkeypatch):
    import app.base_services as base_services

    captured = {}

    class DummyResp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "model": "server-m"}

        def raise_for_status(self):
            pass

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("json") or {}
        return DummyResp()

    monkeypatch.setattr(base_services.http_session, "post", fake_post)

    cfg = {
        "server_url": "http://example.invalid/v1/chat/completions",
        "api_model": "openai/gpt-oss-20b",
    }
    svc = LLMService(config=cfg)
    svc.mode = "server"
    res = svc.generate("hi", response_format="harmony")
    assert res["text"] == "ok"
    assert "response_format" not in captured["payload"]


def test_generate_forwards_text_response_format_in_server_mode(monkeypatch):
    import app.base_services as base_services

    captured = {}

    class DummyResp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "model": "server-m"}

        def raise_for_status(self):
            pass

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("json") or {}
        return DummyResp()

    monkeypatch.setattr(base_services.http_session, "post", fake_post)

    cfg = {
        "server_url": "http://example.invalid/v1/chat/completions",
        "api_model": "openai/gpt-oss-20b",
    }
    svc = LLMService(config=cfg)
    svc.mode = "server"
    res = svc.generate("hi", response_format="text")
    assert res["text"] == "ok"
    assert captured["payload"]["response_format"] == {"type": "text"}
