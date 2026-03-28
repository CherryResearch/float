import importlib
from pathlib import Path


def setup_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    if "app.utils.conversation_store" in importlib.sys.modules:
        module = importlib.reload(
            importlib.import_module("app.utils.conversation_store")
        )
    else:
        module = importlib.import_module("app.utils.conversation_store")
    return module


def test_save_and_load(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    messages = [{"role": "user", "content": "hi"}]
    store.save_conversation("abc", messages)
    assert Path(tmp_path, "abc.json").exists()
    loaded = store.load_conversation("abc")
    assert loaded == messages


def test_list_and_delete(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("one", [{"role": "user", "content": "1"}])
    store.save_conversation("empty", [])
    names = store.list_conversations()
    assert "one" in names and "empty" not in names
    store.delete_conversation("one")
    assert Path(tmp_path, "one.json").exists() is False


def test_list_conversations_sorted(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    # Save conversations out of alphabetical order
    store.save_conversation("sess-2", [{"role": "user", "content": "2"}])
    store.save_conversation("sess-1", [{"role": "user", "content": "1"}])
    names = store.list_conversations()
    assert names == ["sess-1", "sess-2"]


def test_list_conversations_with_metadata(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("sess-1", [{"role": "user", "content": "1"}])
    detailed = store.list_conversations(include_metadata=True)
    assert isinstance(detailed, list)
    assert isinstance(detailed[0], dict)
    assert detailed[0]["name"] == "sess-1"
    assert detailed[0]["updated_at"]
    assert detailed[0]["created_at"]
    assert detailed[0]["display_name"]


def test_display_name_flags(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("sess-1", [{"role": "user", "content": "1"}])
    store.set_display_name("sess-1", "Project kickoff", auto_generated=True)
    meta = store.get_metadata("sess-1")
    assert meta["display_name"] == "Project kickoff"
    assert meta["auto_title_applied"] is True
    store.set_display_name("sess-1", "Manual Title", manual=True)
    meta = store.get_metadata("sess-1")
    assert meta["manual_title"] is True


def test_rename(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("src", [{"role": "user", "content": "x"}])
    store.rename_conversation("src", "dest")
    assert Path(tmp_path, "dest.json").exists()
    assert store.load_conversation("dest")[0]["content"] == "x"
    meta = store.get_metadata("dest")
    assert meta["display_name"] == "dest"
    assert meta["manual_title"] is True


def test_rename_preserves_metadata(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("src", [{"role": "user", "content": "x"}])
    before = store.list_conversations(include_metadata=True)[0]["updated_at"]
    store.rename_conversation("src", "dest")
    detailed = store.list_conversations(include_metadata=True)
    assert detailed[0]["name"] == "dest"
    assert detailed[0]["updated_at"] == before


def test_nested_conversation_paths(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("projects/alpha", [{"role": "user", "content": "x"}])
    assert (tmp_path / "projects" / "alpha.json").exists()
    names = store.list_conversations()
    assert names == ["projects/alpha"]
    detailed = store.list_conversations(include_metadata=True)
    assert detailed[0]["name"] == "projects/alpha"
    assert detailed[0]["display_name"] == "alpha"


def test_move_preserves_display_name(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("sess-1", [{"role": "user", "content": "1"}])
    store.set_display_name("sess-1", "Project kickoff", auto_generated=True)
    store.rename_conversation("sess-1", "work/sess-1")
    meta = store.get_metadata("work/sess-1")
    assert meta["display_name"] == "Project kickoff"
    assert meta["auto_title_applied"] is True


def test_dev_mode_uses_test_folder(monkeypatch):
    monkeypatch.delenv("FLOAT_CONV_DIR", raising=False)
    monkeypatch.setenv("FLOAT_DEV_MODE", "true")
    module = importlib.import_module("app.utils.conversation_store")
    module = importlib.reload(module)
    assert module.CONV_DIR.name == "test_conversations"


def test_conversation_id_sidecar(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("abc", [{"role": "user", "content": "hi"}])
    cid1 = store.get_or_create_conversation_id("abc")
    cid2 = store.get_or_create_conversation_id("abc")
    assert cid1 == cid2
    meta = tmp_path / "abc.meta.json"
    assert meta.exists()
    # Rename should move meta
    store.rename_conversation("abc", "renamed")
    cid3 = store.get_or_create_conversation_id("renamed")
    assert cid3 == cid1
    assert not meta.exists()
    assert (tmp_path / "renamed.meta.json").exists()
