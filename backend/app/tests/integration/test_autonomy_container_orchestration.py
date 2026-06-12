from __future__ import annotations

import os
import time
from typing import Any, Dict

import pytest
import requests


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


pytestmark = [
    pytest.mark.api,
    pytest.mark.autonomy,
    pytest.mark.integration,
    pytest.mark.skipif(
        not _truthy(os.getenv("FLOAT_RUN_AUTONOMY_INTEGRATION_TESTS")),
        reason="set FLOAT_RUN_AUTONOMY_INTEGRATION_TESTS=1 to run autonomy integration tests",
    ),
]


def _api_base() -> str:
    return os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def _headers() -> Dict[str, str]:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY is required for the live container test")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, **kwargs) -> Dict[str, Any]:
    response = requests.request(
        method,
        f"{_api_base()}{path}",
        headers=_headers(),
        timeout=kwargs.pop("timeout", 30),
        **kwargs,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"text": response.text}
    assert response.status_code < 400, payload
    return payload


def _extract_output_text(response: Dict[str, Any]) -> str:
    parts: list[str] = []
    for item in response.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def test_openai_container_background_response_smoke():
    model = os.getenv("FLOAT_AUTONOMY_TEST_MODEL", "gpt-4.1-mini")
    container: Dict[str, Any] | None = None
    try:
        container = _request(
            "POST",
            "/containers",
            json={
                "name": f"float-autonomy-smoke-{int(time.time())}",
                "memory_limit": "1g",
            },
        )
        container_id = container.get("id")
        assert container_id, container

        created = _request(
            "POST",
            "/responses",
            json={
                "model": model,
                "background": True,
                "instructions": (
                    "You must use the code_interpreter tool for this request. "
                    "Do not answer from text-only reasoning."
                ),
                "tools": [
                    {
                        "type": "code_interpreter",
                        "container": container_id,
                    }
                ],
                "tool_choice": "required",
                "input": (
                    "Use python in the attached container to write "
                    "/mnt/data/float_autonomy_probe.txt containing the platform "
                    "name, then read it back. Return one short line containing "
                    "FLOAT_AUTONOMY_OK and that platform name."
                ),
            },
            timeout=60,
        )
        response_id = created.get("id")
        assert response_id, created

        deadline = time.time() + int(os.getenv("FLOAT_AUTONOMY_TEST_TIMEOUT", "180"))
        final = created
        while time.time() < deadline:
            final = _request("GET", f"/responses/{response_id}", timeout=30)
            if final.get("status") in {
                "completed",
                "failed",
                "cancelled",
                "incomplete",
            }:
                break
            time.sleep(5)

        assert final.get("status") == "completed", final
        output_text = _extract_output_text(final)
        assert "FLOAT_AUTONOMY_OK" in output_text
        assert any(token in output_text.lower() for token in ["linux", "ubuntu"])
    finally:
        container_id = (container or {}).get("id")
        if container_id:
            try:
                requests.delete(
                    f"{_api_base()}/containers/{container_id}",
                    headers=_headers(),
                    timeout=30,
                )
            except Exception:
                pass
