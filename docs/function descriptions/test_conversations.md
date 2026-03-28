# Test Conversation JSON Guidelines

Place test conversation files under `backend/app/tests/prompts/`. Each file should
be a valid JSON array representing a conversation. Use the following guidelines:

- **Structure**: Represent conversations as lists of turns. Multi-message turns
  can be expressed as nested arrays.
- **Roles**: Each message object must include a `role` and `content` field.
- **Multimodal markers**: Use placeholders such as `[audio:sample.wav]` or
  `[image: eiffel.jpg]` to indicate audio or image references. Tools can be
  invoked with `[tool: name {"arg": "value"}]` syntax.
- **Naming**: Use descriptive snake_case filenames like
  `audio_transcription.json` or `image_retrieval.json`.
- **Maintenance**: Keep prompts concise and focused. Remove obsolete files and
  ensure new prompts pass linting and tests with `poetry run pre-commit` and
  `poetry run pytest`.
