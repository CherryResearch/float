from __future__ import annotations

from app.local_providers.ollama import OllamaAdapter


def test_ollama_capabilities_expose_context_length_and_load_controls():
    adapter = OllamaAdapter()
    capabilities = adapter.capabilities({"local_provider_mode": "remote-unmanaged"})
    assert capabilities["start_stop"] is False
    assert capabilities["load_unload"] is True
    assert capabilities["context_length"] is True
    assert capabilities["logs_stream"] is False
