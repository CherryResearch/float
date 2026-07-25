import sys
from pathlib import Path

import jwt
import pytest


@pytest.fixture
def receipt_module(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.utils import device_registry, sync_plan_receipt, user_settings

    monkeypatch.setattr(
        user_settings, "USER_SETTINGS_PATH", tmp_path / "user_settings.json"
    )
    monkeypatch.setattr(device_registry, "DEVICES_PATH", tmp_path / "devices.json")
    return sync_plan_receipt


def _receipt_payloads():
    context = {
        "peer_id": "peer-pear",
        "remote_base_url": "http://pear.local:5000",
        "previewed_sections": ["conversations", "settings"],
    }
    allowed = {
        "pull": {"conversations": ["conv-a", "conv-b"], "settings": ["settings"]},
        "push": {"conversations": ["conv-c"]},
    }
    freshness = {
        "local": {"conversations": "local-a", "settings": "local-settings"},
        "remote": {"conversations": "remote-a", "settings": "remote-settings"},
    }
    return context, allowed, freshness


def test_sync_plan_receipt_allows_reviewed_subset(receipt_module):
    context, allowed, freshness = _receipt_payloads()
    token = receipt_module.issue_sync_plan_receipt(
        context=context, allowed=allowed, freshness=freshness
    )
    claims = receipt_module.decode_sync_plan_receipt(token)

    receipt_module.assert_sync_plan_authorized(
        claims,
        context=context,
        direction="pull",
        sections=["conversations"],
        item_selections={"conversations": ["conv-b"]},
        freshness={
            "local": {"conversations": "local-a"},
            "remote": {"conversations": "remote-a"},
        },
    )


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"context": {"peer_id": "other"}}, "settings changed"),
        ({"item_selections": {"conversations": ["conv-z"]}}, "not included"),
        ({"item_selections": {}}, "explicit reviewed"),
        (
            {
                "freshness": {
                    "local": {"conversations": "changed"},
                    "remote": {"conversations": "remote-a"},
                }
            },
            "data changed",
        ),
    ],
)
def test_sync_plan_receipt_rejects_changed_or_unreviewed_apply(
    receipt_module, changes, match
):
    context, allowed, freshness = _receipt_payloads()
    claims = receipt_module.decode_sync_plan_receipt(
        receipt_module.issue_sync_plan_receipt(
            context=context, allowed=allowed, freshness=freshness
        )
    )
    call = {
        "context": context,
        "direction": "pull",
        "sections": ["conversations"],
        "item_selections": {"conversations": ["conv-a"]},
        "freshness": {
            "local": {"conversations": "local-a"},
            "remote": {"conversations": "remote-a"},
        },
    }
    call.update(changes)

    with pytest.raises(ValueError, match=match):
        receipt_module.assert_sync_plan_authorized(claims, **call)


def test_sync_plan_receipt_expires_and_rejects_tampering(receipt_module):
    context, allowed, freshness = _receipt_payloads()
    expired = receipt_module.issue_sync_plan_receipt(
        context=context,
        allowed=allowed,
        freshness=freshness,
        now=1,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        receipt_module.decode_sync_plan_receipt(expired)

    valid = receipt_module.issue_sync_plan_receipt(
        context=context, allowed=allowed, freshness=freshness
    )
    header, payload, signature = valid.split(".")
    offset = len(payload) // 2
    replacement = "a" if payload[offset] != "a" else "b"
    tampered_payload = f"{payload[:offset]}{replacement}{payload[offset + 1:]}"
    with pytest.raises(jwt.InvalidTokenError):
        receipt_module.decode_sync_plan_receipt(
            f"{header}.{tampered_payload}.{signature}"
        )
