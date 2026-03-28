import json
from pathlib import Path

import pytest
from app.base_services import LLMService

PROMPTS_DIR = Path(__file__).parent / "prompts"
MODELS = ["gpt-4o", "gpt-3.5-turbo"]


def _last_user_message(conversation):
    """Return the last user message in a conversation structure."""
    if conversation and isinstance(conversation[0], list):
        messages = [msg for turn in conversation for msg in turn]
    else:
        messages = conversation
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg["content"]
    return ""


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("api", marks=pytest.mark.api),
        pytest.param("local", marks=pytest.mark.local),
    ],
)
@pytest.mark.parametrize("prompt_file", sorted(PROMPTS_DIR.glob("*.json")))
@pytest.mark.parametrize("model", MODELS)
def test_prompt_logging(prompt_file, model, mode, monkeypatch):  # noqa: E501
    """Log LLM responses for prompts across API and local modes."""

    # Mock generate methods to avoid real network calls
    def fake_generate(self, prompt, ctx, **kwargs):
        return {
            "text": f"You said: {prompt}",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(LLMService, "_generate_via_api", fake_generate)
    monkeypatch.setattr(LLMService, "_generate_via_local", fake_generate)

    service = LLMService(
        mode=mode,
        config={"api_key": None, "api_model": model, "api_url": "http://test"},
    )

    data = json.loads(prompt_file.read_text())
    user_prompt = _last_user_message(data)
    response = service.generate(user_prompt)

    print(
        f"mode={mode} model={model} prompt={prompt_file.name} "
        f"response={response['text']}"
    )

    assert response["text"].startswith("You said:")
