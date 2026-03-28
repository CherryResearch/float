import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.main import app
    from app.utils import conversation_store, user_settings

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path)
    settings_file = tmp_path / "user_settings.json"
    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", settings_file)
    return TestClient(app)


def test_nested_conversation_name_roundtrip_and_rename(client):
    nested_name = "projects/alpha"
    encoded = quote(nested_name, safe="")
    payload = {
        "name": "Alpha conversation",
        "messages": [{"role": "user", "content": "hello folders"}],
    }

    save_resp = client.post(f"/conversations/{encoded}", json=payload)
    assert save_resp.status_code == 200

    get_resp = client.get(f"/conversations/{encoded}")
    assert get_resp.status_code == 200
    assert get_resp.json()["messages"][0]["content"] == "hello folders"

    rename_resp = client.post(
        f"/conversations/{encoded}/rename",
        json={"new_name": "projects/archive/alpha"},
    )
    assert rename_resp.status_code == 200

    detailed = client.get("/conversations", params={"detailed": True})
    assert detailed.status_code == 200
    names = {entry["name"] for entry in detailed.json()["conversations"]}
    assert "projects/archive/alpha" in names
    assert "projects/alpha" not in names

    encoded_new = quote("projects/archive/alpha", safe="")
    delete_resp = client.delete(f"/conversations/{encoded_new}")
    assert delete_resp.status_code == 200

    listed = client.get("/conversations")
    assert listed.status_code == 200
    assert "projects/archive/alpha" not in listed.json()["conversations"]


def test_nested_conversation_name_export_json(client):
    nested_name = "work/planning"
    encoded = quote(nested_name, safe="")
    payload = {
        "name": "Planning",
        "messages": [{"role": "user", "content": "ship it"}],
    }
    assert client.post(f"/conversations/{encoded}", json=payload).status_code == 200

    export_resp = client.get(
        f"/conversations/{encoded}/export",
        params={"format": "json"},
    )
    assert export_resp.status_code == 200
    body = export_resp.json()
    assert isinstance(body.get("messages"), list)
    assert body["messages"][0]["content"] == "ship it"


def test_suggest_name_route_for_nested_conversation(client):
    nested_name = "projects/title_suggestion"
    encoded = quote(nested_name, safe="")
    payload = {
        "name": "Untitled",
        "messages": [
            {"role": "user", "content": "plan release checklist and QA sequence"},
            {"role": "assistant", "content": "Sure, here is a release plan."},
        ],
    }
    assert client.post(f"/conversations/{encoded}", json=payload).status_code == 200

    suggest_resp = client.get(f"/conversations/{encoded}/suggest-name")
    assert suggest_resp.status_code == 200
    suggested = str(suggest_resp.json().get("suggested_name") or "").strip()
    assert suggested
    assert "/" not in suggested
    assert suggested != "title_suggestion"


def test_reveal_conversation_supports_nested_name(client, monkeypatch):
    from app import routes

    nested_name = "project/reports"
    encoded = quote(nested_name, safe="")
    payload = {"name": "Reports", "messages": [{"role": "user", "content": "q1"}]}
    assert client.post(f"/conversations/{encoded}", json=payload).status_code == 200

    monkeypatch.setattr(routes.subprocess, "Popen", lambda *_args, **_kwargs: None)
    reveal_resp = client.get(f"/conversations/reveal/{encoded}")
    assert reveal_resp.status_code == 200
    reveal_payload = reveal_resp.json()
    assert Path(reveal_payload["path"]).as_posix().endswith("project/reports.json")


def test_import_route_not_shadowed_by_conversation_save(client):
    import_resp = client.post(
        "/conversations/import",
        json={
            "name": "imports/demo",
            "format": "json",
            "messages": [{"role": "user", "content": "from import"}],
        },
    )
    assert import_resp.status_code == 200
    assert import_resp.json()["status"] == "imported"

    encoded = quote("imports/demo", safe="")
    get_resp = client.get(f"/conversations/{encoded}")
    assert get_resp.status_code == 200
    assert get_resp.json()["messages"][0]["content"] == "from import"


def test_import_route_zip_payload(client, monkeypatch):
    import io
    import json
    import zipfile

    payload = {
        "messages": [
            {"role": "user", "text": "Hello from zip"},
            {"role": "ai", "text": "Hello from model"},
        ]
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("chat-export.json", json.dumps(payload))
    archive.seek(0)

    import_resp = client.post(
        "/conversations/import",
        files={"file": ("openai-export.zip", archive.read(), "application/zip")},
        data={"format": "zip", "name": "imports/zip-test"},
    )
    assert import_resp.status_code == 200
    data = import_resp.json()
    assert data["status"] == "imported"
    assert data["name"] == "imports/zip-test"

    encoded = quote("imports/zip-test", safe="")
    get_resp = client.get(f"/conversations/{encoded}")
    assert get_resp.status_code == 200
    messages = get_resp.json()["messages"]
    assert messages[0]["content"] == "Hello from zip"
    assert messages[1]["content"] == "Hello from model"


def test_import_route_zip_preview(client):
    import io
    import json
    import zipfile

    first = {
        "messages": [
            {"role": "user", "text": "first message"},
            {"role": "assistant", "text": "second"},
        ]
    }
    second = {
        "messages": [
            {"role": "user", "text": "only message"},
        ]
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("chats/folder/first.json", json.dumps(first))
        zf.writestr("chats/folder/second.json", json.dumps(second))
    archive.seek(0)

    preview_resp = client.post(
        "/conversations/import/preview",
        files={"file": ("openai-export.zip", archive.read(), "application/zip")},
    )
    assert preview_resp.status_code == 200
    detected = preview_resp.json()["detected_files"]
    assert [item["path"] for item in detected] == [
        "chats/folder/first.json",
        "chats/folder/second.json",
    ]
    assert detected[0]["message_count"] == 2
    assert detected[1]["message_count"] == 1


def test_import_route_json_preview(client):
    import json

    payload = {
        "conversations": [
            {
                "id": "chatgpt-session-1",
                "title": "Session 1",
                "messages": [{"role": "user", "text": "first"},
                             {"role": "assistant", "text": "reply"}],
            },
            {
                "id": "chatgpt-session-2",
                "title": "Session 2",
                "messages": [{"role": "user", "text": "only one"}],
            },
            {
                "id": "chatgpt-session-3",
                "title": "Session 3",
                "messages": [
                    {"role": "user", "text": "history"},
                    {"role": "assistant", "text": "restored"},
                    {"role": "user", "text": "done"},
                ],
            },
        ]
    }
    preview_resp = client.post(
        "/conversations/import/preview",
        files={"file": ("openai-export.json", json.dumps(payload), "application/json")},
    )
    assert preview_resp.status_code == 200
    detected = preview_resp.json()["detected_files"]
    assert detected[0]["path"] == "chatgpt-session-3"
    assert detected[0]["message_count"] == 3
    assert detected[1]["path"] == "chatgpt-session-1"
    assert detected[1]["message_count"] == 2
    assert detected[2]["path"] == "chatgpt-session-2"
    assert detected[2]["message_count"] == 1


def test_import_route_json_multi_selection_with_destination_folder(client):
    import json

    payload = {
        "conversations": [
            {
                "id": "chatgpt-session-1",
                "title": "Session 1",
                "messages": [
                    {"role": "user", "text": "first"},
                    {"role": "assistant", "text": "reply"},
                ],
            },
            {
                "id": "chatgpt-session-2",
                "title": "Session 2",
                "messages": [{"role": "user", "text": "only one"}],
            },
        ]
    }
    import_resp = client.post(
        "/conversations/import",
        files={"file": ("openai-export.json", json.dumps(payload), "application/json")},
        data={
            "format": "json",
            "selected_files": json.dumps(["chatgpt-session-1", "chatgpt-session-2"]),
            "destination_folder": "chatgpt",
        },
    )
    assert import_resp.status_code == 200
    payload = import_resp.json()
    assert payload["status"] == "imported"
    assert payload["count"] == 2
    assert {entry["name"] for entry in payload["imports"]} == {
        "chatgpt/chatgpt-session-1",
        "chatgpt/chatgpt-session-2",
    }


def test_import_route_json_multi_requires_preview_when_unselected(client):
    import json

    payload = {
        "conversations": [
            {"id": "chatgpt-session-1", "messages": [{"role": "user", "text": "first"}]},
            {"id": "chatgpt-session-2", "messages": [{"role": "user", "text": "second"}]},
        ]
    }
    import_resp = client.post(
        "/conversations/import",
        files={"file": ("openai-export.json", json.dumps(payload), "application/json")},
        data={"format": "json"},
    )
    assert import_resp.status_code == 400
    assert (
        "multiple conversations" in str(import_resp.json().get("detail", "")).lower()
    )


def test_import_route_zip_multi_selection_with_destination_folder(client):
    import io
    import json
    import zipfile

    first = {
        "messages": [
            {"role": "user", "text": "first message"},
            {"role": "assistant", "text": "reply"},
        ]
    }
    second = {
        "messages": [{"role": "user", "text": "single message"}]
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("chats/first.json", json.dumps(first))
        zf.writestr("chats/second.json", json.dumps(second))
    archive.seek(0)

    import_resp = client.post(
        "/conversations/import",
        files={"file": ("openai-export.zip", archive.read(), "application/zip")},
        data={
            "format": "zip",
            "selected_files": json.dumps(["chats/first.json", "chats/second.json"]),
            "destination_folder": "chatgpt",
        },
    )
    assert import_resp.status_code == 200
    payload = import_resp.json()
    assert payload["status"] == "imported"
    assert payload["count"] == 2
    assert {entry["name"] for entry in payload["imports"]} == {
        "chatgpt/first",
        "chatgpt/second",
    }


def test_import_route_zip_destination_sanitizes_unsafe_segments(client):
    import io
    import json
    import zipfile

    payload = {
        "messages": [
            {"role": "user", "text": "safety test"},
        ]
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("first.json", json.dumps(payload))
    archive.seek(0)

    import_resp = client.post(
        "/conversations/import",
        files={"file": ("openai-export.zip", archive.read(), "application/zip")},
        data={
            "format": "zip",
            "selected_files": json.dumps(["first.json"]),
            "destination_folder": "../chatgpt/..//",
        },
    )
    assert import_resp.status_code == 200
    payload = import_resp.json()
    assert payload["status"] == "imported"
    assert payload["name"] == "chatgpt/first"
