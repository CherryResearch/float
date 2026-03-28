import jwt
import pytest
from app.services.sync_service import SyncService


def test_destination_management(tmp_path):
    cfg = tmp_path / "dest.json"
    svc = SyncService(secret_key="secret", config_path=cfg)

    svc.add_destination("onedrive", "https://example.com/onedrive")
    assert "onedrive" in svc.list_destinations()

    svc.remove_destination("onedrive")
    assert "onedrive" not in svc.list_destinations()


def test_jwt_authentication(tmp_path):
    cfg = tmp_path / "dest.json"
    svc = SyncService(secret_key="another", config_path=cfg)

    token = svc.generate_token({"sub": "tester"})
    payload = svc.verify_token(token)
    assert payload["sub"] == "tester"

    with pytest.raises(jwt.InvalidTokenError):
        svc.verify_token("badtoken")
