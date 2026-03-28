from app.utils import conversation_io


def test_export_markdown_includes_thought_summary():
    messages = [
        {"role": "user", "text": "Hello"},
        {
            "role": "ai",
            "text": "Hi there",
            "thought_trace": [
                {"index": 0, "text": "Need", "timestamp": 1.0},
                {"index": 1, "text": " to respond", "timestamp": 2.0},
            ],
            "metadata": {"status": "complete"},
        },
    ]
    md = conversation_io.export_conversation_markdown(
        name="test", messages=messages, metadata={"id": "abc"}
    )
    assert "#### thoughts" in md
    assert "thoughts: 3 tokens" in md


def test_import_markdown_round_trip_role_and_text():
    md = """# Conversation Export

## Messages
### [user] id=one ts=2026-01-30T00:00:00Z
Hello there

### [assistant] id=two ts=2026-01-30T00:00:01Z
Hi!

#### thoughts
thoughts: 2 tokens, 1s, 2 responses: Need reply
"""
    messages = conversation_io.import_conversation_markdown(md)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["text"] == "Hello there"
    assert messages[1]["role"] == "ai"
    assert messages[1]["text"] == "Hi!"


def test_export_markdown_includes_tools_when_requested():
    messages = [
        {
            "role": "ai",
            "text": "Here",
            "tools": [
                {
                    "name": "recall",
                    "status": "invoked",
                    "args": {"key": "profile"},
                    "result": {"ok": True},
                }
            ],
        }
    ]
    md = conversation_io.export_conversation_markdown(
        name="test", messages=messages, include_tools=True
    )
    assert "- [x] recall (invoked)" in md


def test_export_markdown_excludes_chat_when_disabled():
    messages = [
        {"role": "user", "text": "Hello"},
        {"role": "ai", "text": "Hi"},
    ]
    md = conversation_io.export_conversation_markdown(
        name="test",
        messages=messages,
        include_chat=False,
        include_thoughts=False,
        include_tools=False,
    )
    assert "Hello" not in md
    assert "Hi" not in md


def test_import_openai_mapping_payload():
    payload = {
        "mapping": {
            "m1": {
                "message": {
                    "author": {"role": "user"},
                    "create_time": 100,
                    "content": {"parts": ["Need weather update"]},
                }
            },
            "m2": {
                "message": {
                    "author": {"role": "assistant"},
                    "create_time": 101,
                    "content": {"parts": ["Got it."]},
                }
            },
        }
    }
    messages = conversation_io.import_openai_conversation_json(payload)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["text"] == "Need weather update"
    assert messages[1]["role"] == "ai"
    assert messages[1]["text"] == "Got it."


def test_import_openai_zip_payload():
    import io
    import json
    import zipfile

    zipped = io.BytesIO()
    payload = {
        "messages": [
            {"role": "user", "text": "Hello zip"},
            {"role": "assistant", "text": "Hello back"},
        ]
    }
    with zipfile.ZipFile(zipped, "w") as archive:
        archive.writestr("chat.json", json.dumps(payload))
    messages = conversation_io.import_openai_conversation_zip(zipped.getvalue())
    assert [msg["text"] for msg in messages] == ["Hello zip", "Hello back"]


def test_import_openai_zip_chooses_longest_message_payload():
    import io
    import json
    import zipfile

    payload_messages = {
        "messages": [
            {"role": "user", "text": "conversation zip"},
            {"role": "assistant", "text": "selected by size"},
        ]
    }
    payload_meta = {
        "export": {"name": "meta-only"},
        "info": {"created": "2026-01-01T00:00:00Z"},
    }
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as archive:
        archive.writestr("metadata.json", json.dumps(payload_meta))
        archive.writestr("conversations/session-1.json", json.dumps(payload_messages))
    messages = conversation_io.import_openai_conversation_zip(zipped.getvalue())
    assert [msg["text"] for msg in messages] == ["conversation zip", "selected by size"]


def test_list_openai_json_candidates():
    import json

    payload = {
        "conversations": [
            {"id": "conv-1", "title": "One", "messages": [{"role": "user", "text": "a"}]},
            {
                "uuid": "conv-2",
                "name": "Two",
                "mapping": {
                    "1": {
                        "message": {
                            "author": {"role": "user"},
                            "create_time": 1,
                            "content": {"parts": ["b"]},
                        }
                    }
                },
            },
        ]
    }
    data = json.dumps(payload).encode("utf-8")
    detected = conversation_io.list_openai_conversation_json_candidates(data)
    assert len(detected) == 2
    assert detected[0]["path"] == "conv-1"
    assert detected[0]["message_count"] == 1
    assert detected[1]["path"] == "conv-2"
    assert detected[1]["message_count"] == 1


def test_extract_openai_json_conversations_selected():
    import json

    payload = {
        "conversations": [
            {
                "id": "conv-1",
                "title": "One",
                "messages": [{"role": "user", "text": "a"}],
            },
            {
                "id": "conv-2",
                "title": "Two",
                "messages": [{"role": "user", "text": "b"}, {"role": "assistant", "text": "c"}],
            },
        ]
    }
    extracted = conversation_io.extract_openai_json_conversations(
        json.dumps(payload).encode("utf-8"), selected_files=["conv-2"]
    )
    assert set(extracted.keys()) == {"conv-2"}
    assert [msg["text"] for msg in extracted["conv-2"]] == ["b", "c"]
