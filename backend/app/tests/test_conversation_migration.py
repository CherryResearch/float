import importlib
from pathlib import Path


def _load_store(monkeypatch, conv_dir: Path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(conv_dir))
    if "app.utils.conversation_store" in importlib.sys.modules:
        module = importlib.reload(
            importlib.import_module("app.utils.conversation_store")
        )
    else:
        module = importlib.import_module("app.utils.conversation_store")
    return module


def test_migrate_legacy_conversations_moves_json(monkeypatch, tmp_path: Path):
    legacy = tmp_path / "legacy_conversations"
    target = tmp_path / "data" / "conversations"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "sess-1.json").write_text("[]", encoding="utf-8")
    (legacy / "sess-1.meta.json").write_text("{}", encoding="utf-8")

    # Ensure module import doesn't touch the real repo directories.
    store = _load_store(monkeypatch, tmp_path / "override_conv")

    store._migrate_legacy_conversations(legacy_dir=legacy, target_dir=target)

    assert (target / "sess-1.json").exists()
    assert (target / "sess-1.meta.json").exists()
    assert (legacy / "sess-1.json").exists()
    assert (legacy / "sess-1.meta.json").exists()


def test_migrate_legacy_conversations_copies_nested_paths(monkeypatch, tmp_path: Path):
    legacy = tmp_path / "legacy_conversations"
    target = tmp_path / "data" / "conversations"
    nested = legacy / "folder"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "sess-2.json").write_text(
        '[{"role":"user","content":"hi"}]', encoding="utf-8"
    )
    (nested / "sess-2.meta.json").write_text(
        '{"display_name":"nested"}', encoding="utf-8"
    )

    store = _load_store(monkeypatch, tmp_path / "override_conv")

    store._migrate_legacy_conversations(legacy_dir=legacy, target_dir=target)

    assert (target / "folder" / "sess-2.json").exists()
    assert (target / "folder" / "sess-2.meta.json").exists()
