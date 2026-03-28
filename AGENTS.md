# AGENTS Instructions

This repository is managed with **Poetry** and **pre-commit**.

## Repository layout
- `backend/` – FastAPI backend application with tests under `backend/app/tests/`.
- `frontend/` – React user interface. 
- `docs/` – documentation and design notes. there are a variety of items that will help describe needs and core goals: feature roadmaps, UI screenshots and plans, and an agent session log that can be updated in between sessions with notes of providing context that isn't in the actual update. recent updates consolidated the setup notes (`environment setup.md`) and added new "function descriptions" references to keep feature specs in sync. there is also a README.md.



## Setup
1. Install Python dependencies:
   ```bash
   poetry install
   ```
2. Activate the environment with `poetry shell` or prefix commands with `poetry run`.
3. (Optional) Install frontend dependencies:
   ```bash
   cd frontend
   npm install
   ```

## Testing
- Format and lint all updated files before committing:
  ```bash
  poetry run pre-commit run --files <file1> <file2>
  ```
  To check the entire repository, use `--all-files`.
- Run the unit tests:
  ```bash
  poetry run pytest -vv
  ```
  You can also use `make test` which runs `pytest` inside a virtual environment.

## UI spot-checks (manual)
- Start backend + frontend with `poetry run float --no-open`. The launcher writes ports to `.dev_state.json` (`frontend_port` is the UI URL).
- Take a headless screenshot (Windows Edge/Chrome), waiting for app readiness first:
  ```bash
  powershell -ExecutionPolicy Bypass -File scripts/capture_ui.ps1 -OutputPath data/screenshots/ui.png -Route "/?tab=threads"
  ```
- Useful variants:
  ```bash
  powershell -ExecutionPolicy Bypass -File scripts/capture_ui.ps1 -OutputPath data/screenshots/ui-mobile.png -Width 390 -Height 844 -Route "/?tab=threads"
  powershell -ExecutionPolicy Bypass -File scripts/capture_ui.ps1 -OutputPath data/screenshots/ui-settings.png -Route "/?tab=settings"
  ```
- This flow is acceptable for manual model-based verification of UI states.

## general
Please test contributions when relevant, but for small changes do not waste energy on setting up the entire environment.
Follow these guidelines to keep contributions consistent and passing CI.
leave concise descriptive comments, and comment to denote when incomplete or placeholder code is used. 
pay attention to the dates of files, commits, etc. when there is an inconsistency, float has already had several internal systems replaced and future updates may not be complete either.

## logging & status updates
- Treat `docs/internal/assistant_session_log.txt` as the long-lived memory store for specs, rationale, bug notes, and progress logs. Append concise, dated entries instead of rewriting human-authored docs. if this becomes too long, then compact the document to combine items. Long term this is not needed for version control - but if more information is required in general, request git history.
- Use `docs/internal/to-do-list.md` as the ephemeral backlog: apply ✅ for completed items, 🟡 for WIP/testing, and leave untouched lines as-is. Keep the existing loose structure and only edit the items you are actively updating.
- Use `docs/internal/issue-tracker.md` as the rolling, issue-based status dashboard. Issues come from the to-do lists or ad hoc notes; the session log stays chronological while this file records the current state, evidence, and verification results. Keep wording aligned with the source list and update only when you have concrete outcomes.


## if you are local 
you can ask the user permission to look in the logs or conversations folder to diagnose what is happening.
