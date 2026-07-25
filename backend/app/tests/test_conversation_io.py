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


def test_markdown_export_round_trip_preserves_literal_control_lines():
    source_messages = [
        {
            "role": "user",
            "text": (
                "Literal transcript example:\n### [assistant]\nNot a real message.\n"
                "\\### [system]\nKeep one literal backslash."
            ),
        },
        {
            "role": "ai",
            "text": (
                "Answer\n#### thoughts\n"
                "thoughts: 3 tokens, 1s, 1 responses: literal content"
            ),
            "thought_trace": [
                {
                    "text": "internal line\n### [user]\nnot a real message",
                    "timestamp": 1.0,
                }
            ],
        },
    ]

    markdown = conversation_io.export_conversation_markdown(
        name="control-lines",
        messages=source_messages,
    )
    classification = conversation_io.classify_conversation_markdown(markdown)
    imported = conversation_io.import_conversation_markdown(markdown)

    assert "\\### [assistant]" in markdown
    assert "\\\\### [system]" in markdown
    assert "\\#### thoughts" in markdown
    assert "- format_version: 2" in markdown
    assert classification["classification"] == "conversation"
    assert classification["message_count"] == 2
    assert [(message["role"], message.get("text")) for message in imported] == [
        ("user", source_messages[0]["text"]),
        ("ai", source_messages[1]["text"]),
    ]


def test_text_export_round_trip_and_legacy_transcript_detection():
    source_messages = [
        {
            "role": "user",
            "text": (
                "Literal transcript example:\n[assistant] Not a real message.\n"
                "\\[system] Keep one literal backslash."
            ),
        },
        {
            "role": "ai",
            "text": "Answer",
            "thought_trace": [
                {
                    "text": "internal line\n[user] not a real message",
                    "timestamp": 1.0,
                }
            ],
            "tools": [
                {
                    "name": "lookup",
                    "status": "complete",
                    "args": {"key": "value"},
                    "result": {"ok": True},
                }
            ],
        },
    ]

    exported = conversation_io.export_conversation_text(
        name="project (draft)",
        messages=source_messages,
        metadata={
            "display_name": "Project (draft)",
            "created_at": "2026-07-22T00:00:00Z",
        },
    )
    classification = conversation_io.classify_conversation_text(exported)
    imported = conversation_io.import_conversation_text(exported)

    assert exported.startswith("Float Conversation Text Export\n")
    assert "format_version: 2" in exported
    assert "\\[assistant] Not a real message." in exported
    assert "\\\\[system] Keep one literal backslash." in exported
    assert "- lookup (complete) args=" in exported
    assert "\\- lookup (complete) args=" not in exported
    assert classification["classification"] == "conversation"
    assert classification["message_count"] == 2
    assert [(message["role"], message.get("text")) for message in imported] == [
        ("user", source_messages[0]["text"]),
        ("ai", source_messages[1]["text"]),
    ]

    legacy = "[user] Hello\n\n[ai] Hi\n"
    assert conversation_io.classify_conversation_text(legacy)["classification"] == (
        "conversation"
    )
    assert conversation_io.import_conversation_text(legacy) == [
        {"role": "user", "text": "Hello"},
        {"role": "ai", "text": "Hi"},
    ]


def test_legacy_text_export_suppresses_known_thought_and_tool_rows():
    legacy = """Project (draft) (project (draft))
created_at: 2026-07-22T00:00:00Z

[user] Hello

[ai] Answer
thoughts: 4 tokens, 1s, 1 responses: internal detail
- lookup (complete) args={"key": "value"} result={"ok": true}
"""

    classification = conversation_io.classify_conversation_text(legacy)
    imported = conversation_io.import_conversation_text(legacy)

    assert classification["classification"] == "conversation"
    assert classification["canonical_float_export"] is True
    assert imported == [
        {"role": "user", "text": "Hello"},
        {"role": "ai", "text": "Answer"},
    ]


def test_legacy_text_export_preserves_metadata_examples_inside_balanced_fence():
    legacy = """Legacy notes (legacy-notes)
created_at: 2026-07-22T00:00:00Z

[user] Keep this example:
```text
thoughts: 4 tokens, 1s, 1 responses: literal example
- lookup (complete) args={"query": "alpha  beta"} result={"ok": true}
```

[ai] Real answer
thoughts: 2 tokens, 1s, 1 responses: actual derived metadata
"""

    classification = conversation_io.classify_conversation_text(legacy)
    imported = conversation_io.import_conversation_text(legacy)

    assert classification["classification"] == "conversation"
    assert imported == [
        {
            "role": "user",
            "text": (
                "Keep this example:\n```text\n"
                "thoughts: 4 tokens, 1s, 1 responses: literal example\n"
                '- lookup (complete) args={"query": "alpha  beta"} '
                'result={"ok": true}\n```'
            ),
        },
        {"role": "ai", "text": "Real answer"},
    ]


def test_legacy_text_unterminated_fence_with_metadata_is_ambiguous():
    legacy = """Legacy notes (legacy-notes)
created_at: 2026-07-22T00:00:00Z

[ai] Answer before an uncertain fence
```text
thoughts: 4 tokens, 1s, 1 responses: derived or literal
- lookup (complete) args={"query": "value"} result={"ok": true}
"""

    classification = conversation_io.classify_conversation_text(legacy)

    assert classification["classification"] == "ambiguous"
    assert any(
        "exporter-metadata-shaped" in warning for warning in classification["warnings"]
    )


def test_versioned_exports_keep_structural_headers_after_unclosed_fences():
    source_messages = [
        {"role": "user", "text": "Example starts here:\n```markdown\nstill open"},
        {"role": "ai", "text": "This is the real second message."},
    ]

    markdown = conversation_io.export_conversation_markdown(
        name="unclosed-fence",
        messages=source_messages,
    )
    text = conversation_io.export_conversation_text(
        name="unclosed-fence",
        messages=source_messages,
    )

    assert (
        conversation_io.classify_conversation_markdown(markdown)["message_count"] == 2
    )
    assert conversation_io.classify_conversation_text(text)["message_count"] == 2
    assert conversation_io.import_conversation_markdown(markdown) == source_messages
    assert conversation_io.import_conversation_text(text) == source_messages

    ordinary_markdown = "# Format example\n```markdown\n### [user]\nExample only"
    ordinary_text = "Format example\n```text\n[user] Example only"
    assert (
        conversation_io.classify_conversation_markdown(ordinary_markdown)[
            "classification"
        ]
        == "document"
    )
    assert (
        conversation_io.classify_conversation_text(ordinary_text)["classification"]
        == "document"
    )


def test_unversioned_markdown_keeps_literal_backslash_control_lines():
    legacy = r"""# Conversation Export
- name: legacy

## Messages
### [user]
Keep this exact line:
\### [assistant]
\#### thoughts

### [ai]
Done.
"""

    imported = conversation_io.import_conversation_markdown(legacy)

    assert imported == [
        {
            "role": "user",
            "text": "Keep this exact line:\n\\### [assistant]\n\\#### thoughts",
        },
        {"role": "ai", "text": "Done."},
    ]


def test_classify_valid_float_export_as_conversation():
    markdown = """# Conversation Export
- name: demo

## Messages
### [user]
Hello

### [assistant]
Hi there
"""

    result = conversation_io.classify_conversation_markdown(markdown)

    assert result["classification"] == "conversation"
    assert result["message_count"] == 2
    assert result["role_counts"] == {"user": 1, "ai": 1}
    assert result["canonical_float_export"] is True
    assert result["suggested_action"] == "conversation"


def test_classify_single_message_float_export_as_conversation():
    markdown = """# Conversation Export
- name: one-message

## Messages
### [user]
Only message
"""

    result = conversation_io.classify_conversation_markdown(markdown)

    assert result["classification"] == "conversation"
    assert result["message_count"] == 1


def test_classify_ordinary_markdown_and_unknown_heading_as_documents():
    ordinary = "# Profile\n\nKai likes local-first software."
    unknown_heading = "# Notes\n\n### [notes]\nKeep this as a document."

    ordinary_result = conversation_io.classify_conversation_markdown(ordinary)
    unknown_result = conversation_io.classify_conversation_markdown(unknown_heading)

    assert ordinary_result["classification"] == "document"
    assert ordinary_result["message_count"] == 0
    assert unknown_result["classification"] == "document"
    assert unknown_result["unknown_header_count"] == 1
    assert conversation_io.import_conversation_markdown(unknown_heading) == []


def test_classify_partial_or_suspicious_transcripts_as_ambiguous():
    one_role = "### [user]\nOne transcript-like section."
    banner_inside_message = "### [user]\n# Conversation Export\nNot an envelope."
    prose_then_roles = """# Profile notes
This prose must not disappear.

### [user]
Example question
### [ai]
Example answer
"""

    one_role_result = conversation_io.classify_conversation_markdown(one_role)
    body_banner_result = conversation_io.classify_conversation_markdown(
        banner_inside_message
    )
    preamble_result = conversation_io.classify_conversation_markdown(prose_then_roles)

    assert one_role_result["classification"] == "ambiguous"
    assert one_role_result["message_count"] == 1
    assert body_banner_result["classification"] == "ambiguous"
    assert body_banner_result["canonical_float_export"] is False
    assert preamble_result["classification"] == "ambiguous"
    assert preamble_result["unparsed_content_present"] is True


def test_role_examples_inside_fenced_code_are_not_transcript_messages():
    markdown = """# Format example

```markdown
### [user]
Question
### [assistant]
Answer
```
"""

    result = conversation_io.classify_conversation_markdown(markdown)

    assert result["classification"] == "document"
    assert result["message_count"] == 0


def test_semantic_thoughts_and_tools_headings_remain_message_content():
    markdown = """### [user]
#### Thoughts on architecture
Keep this design paragraph.
### [assistant]
#### Tools for the job
Keep this implementation paragraph too.
"""

    result = conversation_io.classify_conversation_markdown(markdown)
    messages = conversation_io.import_conversation_markdown(markdown)

    assert result["classification"] == "conversation"
    assert "#### Thoughts on architecture" in messages[0]["text"]
    assert "Keep this design paragraph." in messages[0]["text"]
    assert "#### Tools for the job" in messages[1]["text"]
    assert "Keep this implementation paragraph too." in messages[1]["text"]


def test_exact_semantic_headings_in_float_export_remain_message_content():
    markdown = """# Conversation Export
- name: semantic-headings

## Messages
### [user]
#### thoughts
thoughts: 3 tokens, 1s, 2 responses: Literal documentation.
#### tools
- [x] read_file (example) args={} result={}
### [assistant]
Understood.
"""

    result = conversation_io.classify_conversation_markdown(markdown)
    messages = conversation_io.import_conversation_markdown(markdown)

    assert result["classification"] == "conversation"
    assert "#### thoughts" in messages[0]["text"]
    assert "thoughts: 3 tokens, 1s, 2 responses" in messages[0]["text"]
    assert "#### tools" in messages[0]["text"]
    assert "- [x] read_file (example) args={} result={}" in messages[0]["text"]


def test_ambiguous_unknown_heading_is_preserved_in_recognized_message():
    markdown = """### [user]
Keep the following notes.
### [notes]
This body must survive explicit import.
### [assistant]
Understood.
"""

    result = conversation_io.classify_conversation_markdown(markdown)
    messages = conversation_io.import_conversation_markdown(markdown)

    assert result["classification"] == "ambiguous"
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert "### [notes]\nThis body must survive explicit import." in messages[0]["text"]


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


def test_tool_exports_preserve_json_string_whitespace():
    messages = [
        {
            "role": "ai",
            "text": "Done",
            "tools": [
                {
                    "name": "lookup",
                    "status": "complete",
                    "args": {"query": "alpha  beta", "detail": "line  one\nline  two"},
                    "result": {"summary": "gamma  delta"},
                }
            ],
        }
    ]

    markdown = conversation_io.export_conversation_markdown(
        name="tool-whitespace",
        messages=messages,
    )
    text = conversation_io.export_conversation_text(
        name="tool-whitespace",
        messages=messages,
    )

    for exported in (markdown, text):
        assert '"query": "alpha  beta"' in exported
        assert '"detail": "line  one\\nline  two"' in exported
        assert '"summary": "gamma  delta"' in exported


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
            {
                "id": "conv-1",
                "title": "One",
                "messages": [{"role": "user", "text": "a"}],
            },
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


def test_json_candidate_summary_ignores_metadata_only_entries():
    import json

    payload = {
        "conversations": [
            {
                "id": "conv-1",
                "title": "One",
                "messages": [{"role": "user", "text": "a"}],
            },
            {
                "id": "meta-sidecar",
                "title": "Metadata",
                "metadata": {"source": "sidecar"},
            },
        ]
    }
    summary = conversation_io.summarize_openai_conversation_json_candidates(
        json.dumps(payload).encode("utf-8")
    )

    assert summary["importable_conversation_count"] == 1
    assert summary["ignored_json_entry_count"] == 1
    assert [item["path"] for item in summary["detected_files"]] == ["conv-1"]


def test_zip_candidate_summary_counts_ignored_metadata_json():
    import io
    import json
    import zipfile

    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as archive:
        archive.writestr(
            "conversations/session-1.json",
            json.dumps({"messages": [{"role": "user", "text": "conversation"}]}),
        )
        archive.writestr(
            "conversations/session-1.meta.json",
            json.dumps({"id": "sidecar", "message_count": 1}),
        )

    summary = conversation_io.summarize_openai_conversation_zip_candidates(
        zipped.getvalue()
    )

    assert summary["importable_conversation_count"] == 1
    assert summary["ignored_json_file_count"] == 1
    assert summary["ignored_json_files"] == ["conversations/session-1.meta.json"]
    assert summary["detected_files"][0]["path"] == "conversations/session-1.json"


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
                "messages": [
                    {"role": "user", "text": "b"},
                    {"role": "assistant", "text": "c"},
                ],
            },
        ]
    }
    extracted = conversation_io.extract_openai_json_conversations(
        json.dumps(payload).encode("utf-8"), selected_files=["conv-2"]
    )
    assert set(extracted.keys()) == {"conv-2"}
    assert [msg["text"] for msg in extracted["conv-2"]] == ["b", "c"]
