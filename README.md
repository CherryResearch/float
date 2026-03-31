# float 

float is a latent-thought based learning agent designed to run on locally managed hardware with a focus on privacy. float specializes in efficient latent modeling and data collection and is intended to be modular: designed to be augmented by external systems.

please know: float is still in the early stages of development. feedback, testing, and suggestions would be appreciated.

<img width="1920" height="1920" alt="floatlogo_transparent" src="https://github.com/user-attachments/assets/5ac87100-0234-4c72-97b8-7060bfdb407a" />

## Overview

float leverages advanced language models and a modular architecture to provide a robust platform for learning and interaction. It integrates with various tools and APIs to enhance its capabilities. I started working on this app to have a space to learn about AI and create a central, user-controlled platform for researching inference techniques and building domain-specific reinforcement learning sets.

## Status (Working Now vs Planned)

### Working now (alpha)
- Multi-mode chat (API, local, server) with model selection and conversation history stored under `data/conversations/`.
- Built-in tools with approvals and scheduling; tool calls and thought/tool streams show up in the Agent Console.
- Browser-first computer use with shared session-backed tools, screenshot results in chat, native OpenAI computer-tool passthrough for API mode, and an experimental Windows desktop runtime.
- Memory + RAG (Chroma) with Knowledge UI, plus threads/semantic tagging.
- Attachments + media viewer for images, PDFs, and common audio formats.
- Calendar events + scheduled actions/tasks.
- Conversation export/import (markdown/json/text) and history management.
- Conversation import can also ingest OpenAI-style export ZIPs from the History sidebar via file upload (MD/JSON/text/ZIP), currently by selecting a zip and saving a new conversation, but this flow is not yet manually smoke-tested.

### Planned / In progress
- *workflows* chain together models to create a smooth and customizable experience; bounded recursion allows for more complex behavior.
- *streaming* live, voice, and video based interaction with plans to connect to a Float server (pc -> cloud gpu, or pc -> phone) securely.
- *file management* float is intended to work with a desktop environment; control over files in the `data/` directory is a long-term goal.
- *persistence* float is intended to spend more time observing and thinking than responding: independently reasoning about memories, priorities, or tasks while the user is not connected, watching through a live-mode stream, and long-form rolling conversations with context compacting.
- *proactive* float aims to grow into the ability to message the user directly for clarification while reasoning and to suggest tasks and events (for example, a "project review").

## Architecture

- **Language Models**: Local Transformers (GPT-OSS, Qwen 3, Llama 3.1, Gemma) plus OpenAI-compatible API endpoints (OpenAI Responses, LM Studio/Ollama/custom servers). Defaults focus on `gpt-5.4` (API) and `gpt-oss-20b` (local).
- **Data Store**: SQLite is the canonical store for durable memory, knowledge chunks, and the lightweight graph/claim substrate; Chroma is the local retrieval mirror, and Weaviate remains an optional vector backend. Using tool calls or manual user input, float can update, edit, store and reason about memories. ideally, long form content is kept but not fully vectorized for later naive searches alongside automatic RAG memory.
- **Tool Calling**: Built-in tools for memory, web, and local files with approvals/scheduling, plus MCP integration for external tool servers.
- **Modular Design**: Allows for easy replacement of internal models and features.
- **Privacy**: Locally managed data with encrypted memories and selectively masked API calls allows you to use the same knowledge base across models. 


## Setup Instructions

### Prerequisites

- **Python 3.11+**
- **Node.js 16+** with npm (in WSL use NVM)
- **Redis** (for Celery backend)
--optional
- *PostgreSQL* (if using a relational database) #is this still used or is it sqlite now?
- *Docker* (optional, future plan for containerized deployment)

### Dependency Management with Poetry

We recommend using Poetry to manage Python dependencies, extras, and project packaging.

```bash
# Install Poetry (if not already installed)
pip install pipx
pipx install poetry
pipx ensurepath
poetry install
npm install
```
# Activate the project environment
# poetry can not safely package CUDA. once you have activated the env with poetry shell, run this: 
```bash
poetry env activate
# this prints the command to activate the associated env: copy and paste it e.g. & "C:\users\...pypoetry\cache\virtualenvs\ ..."
pip install torch==2.7.1+cu128 torchvision==0.22.1+cu128 torchaudio==2.7.1+cu128 --index-url https://download.pytorch.org/whl/cu128
```
If pip/poetry stalls on large wheels, `uv` can install into the Poetry env:
```bash
poetry run uv pip install --upgrade --force-reinstall --index-url https://download.pytorch.org/whl/cu128 torch==2.7.1+cu128 torchvision==0.22.1+cu128 torchaudio==2.7.1+cu128
```
CPU-only fallback (fixes `torchvision::nms` mismatch errors):
```bash
poetry run uv pip install --upgrade --force-reinstall --index-url https://download.pytorch.org/whl/cpu torch==2.7.1+cpu torchvision==0.22.1+cpu torchaudio==2.7.1+cpu
```
Then launch the servers with the provided CLI:
```bash
poetry run float
```

Optional computer-use bootstrap:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_computer_use.ps1
poetry run python scripts/computer_use_smoke.py --target all
```
This installs the Playwright browser runtime, installs Chromium, and runs direct browser plus Windows smoke checks. On non-Windows hosts, the browser runtime is the primary target and the Windows runtime is intentionally not expected to run.

### API connection

once launched, navigate to settings, and ensure the url points to https://api.openai.com/v1/responses
then add your openai API key from platform.openai.com
  -Float keeps a small default model list, and will also poll the configured provider for available models (via `/api/openai/models`) so newer entries (e.g. `gpt-5.2`) and other OpenAI-compatible providers show up in the selectors.

### Hugging Face tokens (for gated model downloads)

Some local models (e.g. Gemma) are gated on Hugging Face. Create a personal access token at https://huggingface.co/settings/tokens with read access, accept the model license on the repo page, and set it in Settings (HF Token) or via `HF_TOKEN` / `HUGGINGFACE_HUB_TOKEN` in the environment.

## Local Providers
Float can run local inference directly on the machine or through a managed provider.

Managed local providers:

- LM Studio docs: [LM Studio CLI](https://lmstudio.ai/docs/cli)
- Ollama docs: [Ollama Docs](https://ollama.com/docs)

The provider path uses an OpenAI-compatible transport such as `http://<host>:<port>/v1`.
`Server/LAN` is separate: it points at an already-running OpenAI-compatible server via `server_url` and does not use the local provider manager.

If local Transformers fails on BF16/MXFP4/CUDA mismatches, switch to a managed quantized runtime such as `lmstudio` or `ollama`.
If the model you have is a raw `.gguf`, do not treat it like a direct local Transformers checkpoint. Run it behind LM Studio, Ollama, or another OpenAI-compatible server first.

Routing snapshot:

- Chat uses `api`, `local`, or `server` mode.
- Text embeddings use `rag_embedding_model` (`local:*`, `api:*`, or `simple`) and do not automatically follow `server_url`.
- TTS uses OpenAI `tts-1` / `tts-1-hd` or local `kitten` / `kokoro` style models.
- Live voice uses OpenAI Realtime by default, with LiveKit kept as a fallback transport.
- The public runtime overview lives in `docs/feature_overviews/models-and-runtime-modes.md`, with setup details in `docs/environment setup.md`.

## Private Sync, Streaming, and Workspaces
Float should treat sync and live streaming as device-trust problems, not public-account problems.

The secure individual-focused model is:

- Explicit device pairing, not shared login credentials.
- Private transport only, such as LAN, VPN, or a user-operated tunnel.
- Short-lived session grants for sync and streaming.
- Per-feature scopes with revocation, so a device can be trusted for sync but not voice, or voice but not file access.
- No public exposure by default.

OAuth-style login can still make sense for hosted collaboration later, but for a personal Float deployment the first-class path should be trusted devices and scoped sessions.

Workspaces now sit under that same model.

- Every device has one root workspace.
- Additional named workspaces can represent separate local roots such as `work` and `personal`.
- Sync can either:
  - `merge` selected workspaces into a target workspace, or
  - `import nested` so one device's workspace appears as a source-owned nested workspace on another device.
- Imported nested workspaces keep source metadata so syncing back to the origin can ignore that imported copy and avoid recursive trees.

## Instance Sync (Current Preview)

Float now includes a real trusted-device sync surface in `Knowledge > Sync`.
It is still early, but it is no longer just a hidden settings concept.

Current flow:

1. Turn on `Visible on LAN` on the receiving device.
2. Copy that device's advertised LAN URL.
3. Generate a one-time pairing code there.
4. Enter the URL and code on the other device.
5. Choose scopes, workspace mapping, and sync mode.
6. Preview pull/push differences by section.
7. Apply only the sections you want.

Sections currently covered:

- conversations
- memories
- knowledge
- knowledge graph
- attachments
- calendar
- workspace preferences

Current merge behavior is last-write-wins by each section's stored update timestamp. Conversation renames follow the stable conversation sidecar id when available, so folder/title moves survive a sync instead of being treated like unrelated chats.

The sync panel also exposes:

- current device visibility and pairing state,
- saved/paired/connected device states,
- inbound trusted-device state on the host,
- workspace-aware pull/push targets,
- source-linking and nested import behavior,
- import/export from the same surface.

For remote personal-GPU access, LM Studio's [LM Link](https://lmstudio.ai/docs/lmlink) is the cleaner transport story to mention. It is adjacent to Float sync rather than a required Float setting.

If you want one Float instance to reach another machine without exposing it publicly, layer the app on top of a private tunnel or tailnet such as [Tailscale Serve](https://tailscale.com/kb/1312/serve).

For more detail, see:
- `docs/feature_overviews/device-sync-and-streaming.md`
- `docs/data_directory.md`


## Notebooks
Run notebooks in the Poetry environment by installing a kernel once:
`poetry run python -m ipykernel install --user --name float-project --display-name "float (poetry)"`

### Launcher options

All flags are per-run; nothing is persisted except sticky port/browser state in `.dev_state.json`.

- `--dev` / `-dev`: enable dev mode for this run only (sets `FLOAT_DEV_MODE=true` for the process; does not write `.env`).
- `--server` / `--backend-only`: start backend only (skip frontend).
- `--ui` / `--frontend-only`: start frontend only (skip backend).
- `--skip-backend`: do not start the backend server.
- `--skip-frontend`: do not start the frontend server.
- `--backend-port <port>`: set backend port (default: auto-select).
- `--frontend-port <port>`: set frontend port (default: auto-select).
- `--sticky-ports` / `--no-sticky-ports`: reuse last ports or choose new ports each run.
- `--no-open`: do not open a browser tab on launch.
- `--open-once`: open the browser only the first time (sticky across restarts).

### Desktop shortcut (Windows)

To create or update a Desktop launcher named lowercase `float`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/create_desktop_shortcut.ps1
```

This creates `float.lnk` on your Desktop and uses the existing logo asset `frontend/public/floatgpt.png` (converted to `frontend/public/float.ico`) as the shortcut icon. The shortcut launches `poetry run float` from this repository root.

### Developer mode (/dev)

Set `FLOAT_DEV_MODE=true` before starting the backend (or run `poetry run float --dev` for a one-off session) to enable the Dev Panel route. Then navigate to `/dev` to:
- Run built-in test prompts (`/api/test-prompts`).
- Watch live thought/tool logs (`/api/ws/thoughts`).
### WSL/linux-specific steps from scratch

Install nvm
https://learn.microsoft.com/en-us/windows/dev-environment/javascript/nodejs-on-wsl

Commands (in wsl shell)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/master/install.sh | bash

nvm install --lts

Then install redis and postgres 

sudo apt-get install redis

pip install pipx
You will need to open a new terminal or re-login for the PATH changes to take effect.
Alternatively, you can source your shell's config file with e.g. 'source ~/.bashrc'.
 
sudo apt-get install python3-venv
Python3 -m pipx install poetry
Python3 -m pipx ensurepath

### Running Tests

Backend tests are located under `backend/app/tests`. Use Poetry to run them:

```bash
poetry run pytest -vv backend/app/tests

# Run API tests (marked `api`)
poetry run pytest -vv -m api backend/app/tests

# Run local tests (marked `local`)
poetry run pytest -vv -m local backend/app/tests
```

### Data Directory

Runtime artifacts live under `data/` (gitignored so installs stay private):

- `data/databases/{calendar_events,chroma}` — calendars now save JSON entries here and the Chroma vector store persists beside them.
- `data/files/{uploads|screenshots|downloaded|workspace}` — user uploads, captured media, tool downloads, and docs-ingest workspace files share one sandbox.
- `data/models/` — default cache/target for local model downloads (legacy `models/` folders are still detected if present).
- `data/workspace/` — scratch space Float can edit freely during personal-device streaming or tool executions.

See `docs/data_directory.md` for the full layout and usage notes; conversation history lives under `data/conversations/` (legacy `conversations/` is auto-migrated on startup when `FLOAT_CONV_DIR` is unset) and `blobs/` remains beside the repo for now.

### Voice Setup

Float now has two voice transport paths:

- OpenAI Realtime is the current cloud-default path. Set `OPENAI_API_KEY` and keep `FLOAT_STREAM_BACKEND=api` (the current default). `/api/voice/connect` will mint an ephemeral client secret and return the browser-facing Realtime connect URL; the frontend then establishes WebRTC directly to OpenAI.
- LiveKit remains available as a fallback. Set `FLOAT_STREAM_BACKEND=livekit` plus the LiveKit credentials below if you want the older room/token flow instead.

OpenAI Realtime optional settings:

```env
OPENAI_API_KEY=your_openai_key
FLOAT_STREAM_BACKEND=api
OPENAI_REALTIME_MODEL=gpt-realtime
OPENAI_REALTIME_TURN_DETECTION=server_vad
OPENAI_REALTIME_TTL_SECONDS=600
```

LiveKit fallback settings:

```env
FLOAT_STREAM_BACKEND=livekit
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_SECRET=your_livekit_secret
```

In Realtime API mode the browser streams directly to OpenAI, so `/api/voice/stream` is not used. In LiveKit mode, `/api/voice/connect` returns the room token and the older worker-backed streaming path remains available. Live browser verification is still recommended for microphone permissions, turn-taking, and transcript/event surfacing.

### Model Catalog

| Type                         | Local Examples                                      | API Examples                                              | Notes & Extensions                                          |
|------------------------------|-----------------------------------------------------|-----------------------------------------------------------|-------------------------------------------------------------|
| **Turn Detection**            | `webrtcvad`                                         | —                                                         | Speech segmentation via Voice Activity Detection (VAD).     |
| **ASR (Speech-to-Text)**      | `Whisper.cpp`, `Vosk`, **Voxtral (local)**          | OpenAI Whisper API, **Voxtral API**                       | Voxtral supports low-latency, high-quality ASR.              |
| **TTS (Text-to-Speech)**      | `Coqui TTS`, `Piper`, **Voxtral (local)**           | OpenAI TTS API, **Voxtral API**                           | Voxtral excels in multilingual and natural voice synthesis.  |
| **Speech-to-Speech (S2S)**    | **Voxtral (local)**                                 | OpenAI Speech-to-Speech API (Beta), **Voxtral API**       | Voxtral offers streamlined S2S pipelines (local & API).      |
| **LLM (Language Models)**     | Mistral (GPT-OSS/transformers), Gemma, Paligemma (multimodal)     | OpenAI GPT-4, **GPT-4.1 (tool-call optimized)**, Gemini   | GPT-4.1 excels at structured tool calls.                    |
| **CV (Computer Vision)**      | OpenCV, Mediapipe                                   | Google Vision API, OpenAI Vision (GPT-4o)                 | Local CV for gestures/faces; API for OCR & image tasks.      |
| **Embeddings (Text)**         | `sentence-transformers` (all-mpnet-base-v2)         | OpenAI Embeddings API                                     | Local SentenceTransformers are flexible & efficient.         |
| **Embeddings (Multimodal)**   | CLIP (local), OpenAI CLIP (via GPT-4o)              | OpenAI GPT-4o embeddings, Google Gemini                   | For image-text and audio embeddings.                         |
| **Extraction & Summarization** | `LangExtract` | — | Distills transcripts into structured summaries that feed embedding stores. |
| **Observational Context (Visual)** | OpenCV (frame capture), screen-capture scripts     | —                                                         | Snapshots from videos/screens for context injection.         |
| **Observational Context (Audio)**  | Live stream embeddings (Whisper, Voxtral)          | —                                                         | Segment & embed meaningful audio for context.                |
| **Tool Calls / API Tooling**  | Internal ETL tools, MCP schema                      | API Proxy Wrappers, **GPT-4.1 dynamic tool calls**        | GPT-4.1’s tool-call features enhance Float’s agent chains.   |
| **Memory & World Modeling**   | ChromaDB, PostgreSQL Graphs                        | Weaviate                                                  | Embedding stores, graph relationships, proactive updates.    |


Use LangExtract to convert transcripts into concise summaries before embedding them for efficient recall.

## Harmony Message Format

Float structures LLM exchanges with the
[Harmony envelope](https://github.com/openai/harmony).  Messages are built
using the ``openai-harmony`` utilities and contain typed ``content`` blocks.

```python
from openai_harmony import Message, Role

msg = Message.from_role_and_content(Role.USER, "Hello Harmony!")
print(msg.to_dict())
# {"role": "user", "name": None,
#  "content": [{"type": "text", "text": "Hello Harmony!"}]}
```

Pass ``response_format="harmony"`` to ``LLMService.generate`` to receive
Harmony-formatted responses.

## Where to Edit Core Behavior

- System prompt: `backend/app/config.py` (override with `SYSTEM_PROMPT` in `.env`).
- Built-in tool registry: `backend/app/tools/__init__.py` (`BUILTIN_TOOLS`) and UI schemas in `backend/app/tool_specs.py`.
- Public feature overviews: `docs/feature_overviews/README.md`, `docs/feature_overviews/tools-and-actions.md`, and `docs/feature_overviews/models-and-runtime-modes.md`.
- Workflow runbooks and provider/mode coverage: `docs/feature_overviews/voice-live-and-passthrough.md` and `docs/feature_overviews/models-and-runtime-modes.md`. Model readiness defaults live in `backend/config/model_catalog.yaml`.
- SAE inspection and steering remain roadmap work and are not part of the public alpha surface.

## Companion Codex Skills

Float-specific Codex skills live in a separate repository so the app code and the Codex-facing skill prompts can evolve independently:

- `https://github.com/CherryResearch/float-codex-skills`

That repo is intended for Codex skill content and helper workflows related to Float. Keep it separate from this application repo unless a change specifically belongs in Float itself.

### Docker Deployment

1. **Build and Run the Backend Image**:
   ```bash
   # Build using the backend Dockerfile
   docker build -f docker/backend.Dockerfile -t float-backend .
   docker run -p 8000:8000 float-backend
   ```

   The included `Dockerfile` uses Poetry to install dependencies from `pyproject.toml` (without dev dependencies) for reproducible builds.
   Ensure `poetry.lock` is committed alongside `pyproject.toml` to lock versions in production.

## Key Features

- **API Proxy Features**:
  - GET `/api/responses` : Proxy to OpenAI Responses API for listing responses.
  - GET `/api/responses/{response_id}/completions` : Proxy to OpenAI Responses API for retrieving a specific response's completions.
  - GET `/api/transformers/models` : List available GPT-OSS transformer models.
  - POST `/api/transformers/generate` : Generate text with a selected transformer model.
- **Model Context Management**: Manage and display the current model context.
- **Tool Integration**: Add and manage tools for enhanced functionality.
- **Privacy-Focused**: Designed to operate with a focus on user privacy.
- **Thought Streaming**: `/api/stream/thoughts` provides live thoughts and tool
  logs for the agent console. Integration with external providers is in progress.
- **Agent Console Snapshot**: `GET /api/agents/console` hydrates the right-rail cards when reconnecting or refreshing.
- **Approval Levels**: UI setting to require confirmation for risky actions.
- **Data Visualization**: Demo charts in the frontend are built with D3.
- **File Attachments**: Upload images (jpg, png, gif, webp), PDFs, and common
  audio formats and preview them in the media viewer.

### Tool Registration Example
```bash
curl -X POST http://localhost:8000/tools/register -d '{"name":"read_file"}'
curl -X POST http://localhost:8000/tools/invoke \
     -d '{"name":"read_file","args":{"path":"README.md"}}'
```

### Tool Invocation and Model Selection

Float uses the Model Context Protocol to call tools. When the language model
wants to run a tool it emits a small JSON block, for example:

```json
{"name": "search", "parameters": {"q": "weather"}}
```

The backend matches the tool by name, executes it and returns the result.  You
can also use a shorthand such as `[tool: search {"q":"weather"}]` if the model
is fine‑tuned to recognise that format.

The `LLMService` can work with external models as well.  Set environment
variables like `OPENAI_MODEL` or `LOCAL_LLM_URL` and choose the `api`, `local`
or `dynamic` mode to switch between them.  Use `DYNAMIC_MODEL` and
`DYNAMIC_PORT` to start additional local servers when required.

### Supported Models and Defaults

The **Settings** selectors accept any API/local endpoint pair; defaults are predictable and user settings persist. Current main defaults:

- `gpt-5.4` *(OpenAI API default)*
- `gpt-oss-20b` *(local default)*

Additional suggested options (kept available, not restricted):

- `qwen3-8b` / `qwen3-70b` *(transformers/OSS)*
- `llama-3.1-8b` / `llama-3.1-70b` *(transformers/OSS)*
- `gemma-2b` / `gemma-7b` *(transformers/OSS)*
- `gemini-3` *(API)*
- `mistral-7b` *(transformers/OSS)*

You can point to any endpoint+API key+model combo; presets are hardcoded for convenience, and custom entries are allowed. Hugging Face cache clutter (showing unrelated tiny models) is known; the list will be filtered per modality as the download UX improves. Some downloads may need a manual HF fetch and then selection from `data/models/` until reliability is improved.

> GPT-OSS can handle roughly 7B-20B locally on a modern GPU. 120B-class models usually require a remote GPT-OSS server or multi-GPU setup.

## Contributing

External contributions are accepted only after the contributor agrees to the
repository's assignment terms in [CLA.md](CLA.md). Accepted contributions are
assigned to the project operator under the
[Contributor Assignment Agreement](CONTRIBUTOR_ASSIGNMENT_AGREEMENT.md).

## License

This repository is licensed under the GNU Affero General Public License,
version 3 only. See [LICENSE](LICENSE).

