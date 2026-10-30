# Raw API capture custody (release draft)

Raw OpenAI API request/response captures are now off by default. This does not
change the inference request itself. To opt in for a bounded debugging session,
set `FLOAT_OAI_CAPTURE_CONTENT=true` in the local process environment before
running Float. No setting is changed automatically by this patch.

**Captured content is sensitive and not redacted:** prompts, outputs, tool
arguments/results, session/message identifiers, and endpoint information can be
included. Do not enable this against personal conversations unless you intend
to retain that content. Do not publish captures or include them in releases.
Unset the variable or set it to `false` to stop new captures.

## Location and bounds

Captures use the configured `FLOAT_DATA_DIR` (or Float's normal default data
root), under `diagnostics/oai_api/`. There is no import-time capture directory
creation. Random local filenames replace provider-supplied response IDs.
Existing tool-catalog deduplication and complete function argument deltas remain.

- At most 2 MiB per JSON capture; oversized records are skipped, not truncated.
- At most 50 owned captures and 20 MiB of owned capture JSON retained.
- Owned captures older than seven days are removed on the next enabled,
  size-eligible capture attempt. This is lazy retention, not a timed eraser;
  disabling capture does not delete existing files.
- Oldest files are pruned first. Only `capture-<32 hexadecimal digits>.json`
  files in this dedicated directory are managed. Other files, symlinks and
  legacy captures are not migrated, read, or removed.
- An exclusive `.capture.lock` coordinates processes. A busy lock skips the
  capture. After a crash a stale lock can disable capturing; confirm no capture
  process is active before removing that lock manually. Capture never steals it.
- Symlinked diagnostic subdirectories are refused. This is not a sandbox against
  a malicious local process concurrently replacing directories.

Serialization, storage or retention failures drop the diagnostic capture without
failing inference. Limits apply to retained files, not peak in-memory payload
serialization. Cleanup intentionally cannot reach legacy `logs/oai_api/` files.

## Not included

This patch does not change `chat.log`, `llm_server.log`, telemetry, capture/media
attachments, conversation storage, or consent for sending data to providers.
Those separate logging paths need their own custody review; this is not a claim
that all application logs are content-free or bounded. It does not erase any
already captured data.

Verification uses synthetic input only, with sockets blocked and application data
isolated under a temporary directory. No real provider, credentials, personal
session data, or deployment has been exercised.
