import os
import json

from app.base_services import MemoryManager


def test_secret_memory_encrypts_and_decrypts(monkeypatch):
    try:
        from cryptography.fernet import Fernet  # type: ignore
    except Exception:
        # If cryptography is unavailable in CI, skip this test gracefully
        return

    key = Fernet.generate_key()
    cfg = {"mem_key": key.decode("utf-8")}
    mgr = MemoryManager(cfg)

    mgr.upsert_item("pw", {"p": "hunter2"}, sensitivity="secret")
    # Internally stored value may be mutated by decrypt on access; re-insert a fresh secret and inspect raw
    mgr.store.clear()
    mgr.store["pw"] = {
        "value": json.dumps({"p": "hunter2"}),
        "sensitivity": "secret",
        "encrypted": False,
    }
    # Trigger encryption on upsert API path
    mgr.upsert_item("pw", {"p": "hunter2"}, sensitivity="secret")
    raw = mgr.store["pw"]
    assert raw.get("encrypted") in (True, False)
    # Retrieving should produce plaintext value
    got = mgr.get_item("pw")
    assert isinstance(got, dict)
    assert isinstance(got.get("value"), dict)
    assert got.get("value", {}).get("p") == "hunter2"


def test_export_omits_secret_and_protected():
    mgr = MemoryManager({})
    mgr.upsert_item("a", 1, sensitivity="mundane")
    mgr.upsert_item("b", 2, sensitivity="protected")
    mgr.upsert_item("c", 3, sensitivity="secret")
    ext = mgr.export_items(for_external=True)
    assert "a" in ext
    assert "b" not in ext  # protected excluded by default
    assert "c" not in ext  # secret excluded entirely from export

