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
- Memory + RAG (Chroma) with Knowledge UI, plus threads/semantic tagging.
- Attachments + media viewer for images, PDFs, and common audio formats.
- Calendar events + scheduled actions/tasks.
- Trusted-device sync is available as an alpha preview for paired personal devices, with selective pull/push by section and remaining UI polish still deferred.
- Conversation export/import (markdown/json/text) and history management.
- Conversation import can also ingest OpenAI-style export ZIPs from the History sidebar via file upload (MD/JSON/text/ZIP), currently by selecting a zip and saving a new conversation, but this flow is not yet manually smoke-tested.

### Planned / In progress
- *workflows* chain together models to create a smooth and customizable experience; bounded recursion allows for more complex behavior.
- *streaming* live, voice, and video based interaction with plans to connect to a Float server (pc -> cloud gpu, or pc -> phone) securely.
- *file management* float is intended to work with a desktop environment; control over files in the `data/` directory is a long-term goal.
- *persistence* float is intended to spend more time observing and thinking than responding: independently reasoning about memories, priorities, or tasks while the user is not connected, watching through a live-mode stream, and long-form rolling conversations with context compacting.
- *proactive* float aims to grow into the ability to message the user directly for clarification while reasoning and to suggest tasks and events (for example, a "project review").

### Known alpha limits
- Trusted-device sync is usable enough to ship as alpha preview, but copy polish, broader workspace ergonomics, and background-sync style behavior are still deferred.
- Live voice and streaming paths exist, but they are less mature than core text chat and still need broader live browser verification.
- Some import/export and tool-heavy flows have strong targeted test coverage but lighter manual smoke coverage than the main chat path.

## Architecture

- **Language Models**: Local Transformers (GPT-OSS, Qwen 3, Llama 3.1, Gemma) plus OpenAI-compatible API endpoints (OpenAI Responses, LM Studio/Ollama/custom servers). Defaults focus on `gpt-5` (API) and `gpt-oss-20b` (local).
- **Data Store**: SQLite is the canonical store for durable memory, knowledge chunks, and the lightweight graph/claim substrate; Chroma is the local retrieval mirror, and Weaviate remains an optional vector backend. Using tool calls or manual user input, float can update, edit, store and reason about memories. ideally, long form content is kept but not fully vectorized for later naive searches alongside automatic RAG memory.
- **Tool Calling**: Built-in tools for memory, web, and local files with approvals/scheduling, plus MCP integration for external tool servers.
- **Modular Design**: Allows for easy replacement of internal models and features.
- **Privacy**: Locally managed data with encrypted memories and selectively masked API calls allows you to use the same knowledge base across models. 


## Setup Instructions

### Prerequisites

- **Python 3.11+**
- **Node.js 18+** with npm
- **Redis** if you want the worker-backed scheduling/background path
- *Docker* is optional and mainly relevant for containerized or server-oriented setups

### Dependency Management with Poetry

Use Poetry for Python dependencies and package entrypoints.

```bash
# Install Poetry (if not already installed)
pip install pipx
pipx install poetry
pipx ensurepath
poetry install
npm install
```
# Install CUDA-enabled torch separately if you want direct local GPU inference.
```bash
poetry env activate
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
- Built-in tools are intentionally bounded: approvals still apply, read/write scope is limited, and local file writes are sandboxed to managed project areas rather than the general desktop.
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

For the current alpha, this trusted-device flow has enough real two-device validation to ship as an explicit alpha preview, even though the browser copy and broader workspace ergonomics still need polish.

For remote personal-GPU access, LM Studio's [LM Link](https://lmstudio.ai/docs/lmlink) is the cleaner transport story to mention. It is adjacent to Float sync rather than a required Float setting.

If you want one Float instance to reach another machine without exposing it publicly, layer the app on top of a private tunnel or tailnet such as [Tailscale Serve](https://tailscale.com/kb/1312/serve).

For more detail, see:
- `docs/feature_overviews/device-sync-and-streaming.md`
- `docs/data_directory.md`

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

### Compatibility Notes

Treat this section as a conservative compatibility snapshot, not a promise that every listed provider or modality is equally mature in this alpha. The rows below describe the main runtime families Float currently targets; the longer inventory list lives in `docs/Float_Model_Catalog.csv`.

| Area | Current local or managed examples | Current API or server examples | Alpha note |
| --- | --- | --- | --- |
| **Chat runtime** | Direct local Transformers checkpoints such as GPT-OSS, Qwen 3, Llama 3.1, and Gemma; managed local providers such as LM Studio or Ollama | OpenAI Responses and other OpenAI-compatible servers | Core text chat is the most mature surface. Runtime parity still varies by provider and model family. |
| **Retrieval and embeddings** | `sentence-transformers`, local Chroma mirror, SQLite canonical store | OpenAI-compatible embedding providers; optional Weaviate backend | Retrieval is shipped, but provider defaults and ranking behavior are still being tuned for alpha. |
| **Vision and media inputs** | Local image captioning and file/media handling in the app | OpenAI-compatible multimodal vision paths | Image and document attachments are part of the public alpha; broader computer-vision workflows are still exploratory. |
| **Voice and live transport** | Local TTS options plus the older LiveKit-backed fallback path | OpenAI TTS and OpenAI Realtime voice bootstrap | Voice/live paths are available as preview features and remain less mature than text chat. |
| **Tool calling and actions** | Built-in bounded tools, local workspace sandbox, MCP-compatible integrations | OpenAI-compatible tool-calling models | Tool execution is real, but approvals, sandbox limits, and compatibility aliases mean not every tool path is equally mature. |
| **Memory, knowledge, and sync** | SQLite-backed memory/knowledge, Chroma retrieval mirror, trusted-device sync between paired personal devices | Optional private server/LAN deployments and trusted-peer sync targets | This is the current local-first product direction. Broader hosted or gateway-style flows are still planned rather than shipped. |

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

## Where to Customize Float

If you want to change Float rather than just run it, the main entry points are straightforward. The system prompt and release-facing defaults live in `backend/app/config.py`, built-in tools and their schemas live in `backend/app/tools/__init__.py` plus `backend/app/tool_specs.py`, and the current public product behavior is summarized in `docs/feature_overviews/`.

Provider and runtime defaults are still easy to swap. Model readiness defaults live in `backend/config/model_catalog.yaml`, and the runtime/mode behavior is documented in `docs/feature_overviews/models-and-runtime-modes.md` and `docs/feature_overviews/voice-live-and-passthrough.md`. SAE inspection and steering are still roadmap work, not part of the public alpha surface.

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

- **Runtime modes**: Cloud API, direct local Transformers, and OpenAI-compatible local/server runtimes.
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

### Registering Tools

Registering a tool is a two-step flow. First, expose or allow the tool through the backend registry. After that, invoke it with a concrete argument payload the same way the model or the UI would. For the current alpha, keep that mental model simple: register the capability, then call it with reviewed inputs.

The example below shows the shortest local path. `POST /tools/register` makes the named tool available to the runtime, and `POST /tools/invoke` runs it with arguments. In normal chat flows the same tool call would still be subject to Float's approval and sandbox rules.

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

The backend matches the tool by name, executes it, and returns the result. You
can also use a shorthand such as `[tool: search {"q":"weather"}]` if the model
is fine-tuned to recognise that format.

The `LLMService` can work with external models as well. Set environment
variables like `OPENAI_MODEL` or `LOCAL_LLM_URL` and choose the `api`, `local`,
or `server` mode to switch between them.

### Supported Models and Defaults

Treat the models below as provisional suggestions rather than a tested support matrix. Only a smaller subset is exercised regularly, and many of these placeholders will likely be replaced by newer models before they are treated as first-class defaults.

The **Settings** selectors accept any API/local endpoint pair; defaults are predictable and user settings persist. Current main defaults:

- `gpt-5.1` *(OpenAI API default)*
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

## Network Use and Source Availability

If you deploy Float for users over a network, provide them with the exact Corresponding Source for the running build, including your local modifications and the matching license files. For this release flow, the simplest path is to publish the exact release snapshot repo and commit that the hosted instance is running.

## License

This repository is licensed under the GNU Affero General Public License,
version 3 only. See [LICENSE](LICENSE).

