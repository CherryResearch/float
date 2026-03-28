# Environment Setup Guide

This document outlines the steps to set up the development environment for the Float project, covering both the backend and frontend components.

---

## Prerequisites

- **Python 3.8+**
- **Poetry** for Python package management.
- **Node.js 16+** with npm for frontend development.
- **Docker** (Optional, for containerized deployment).
- **Redis** (Optional, for Celery task queue).
- **PostgreSQL** (Optional, for relational database features).

---

## Backend Setup

The backend is a FastAPI application managed with Poetry.

### 1. Install Dependencies

First, install the required Python packages using Poetry. This will create a virtual environment for the project.

```bash
poetry install
```

Hugging Face Hub is configured with the XET backend through extras in Poetry.
The project pins `huggingface-hub` with `hf-xet` and `hf-transfer` extras, so
no additional system dependencies (like git‑lfs) are required for model
downloads.

To install optional dependencies for Celery workers, you can run:
```bash
poetry install --extras "workers"
```

### CUDA-enabled PyTorch (required for local GPU inference)

Poetry pins the platform-agnostic `torch` package, but CUDA wheels live on
vendor-specific indexes that cannot be encoded in `pyproject.toml`. After the
initial install, activate the Poetry environment and replace the CPU wheel with
the matching CUDA build, then add the MXFP4 kernels:

```bash
poetry shell
python -m pip install --force-reinstall --index-url https://download.pytorch.org/whl/cu128 torch==2.7.1+cu128 torchvision==0.22.1+cu128 torchaudio==2.7.1+cu128
python -m pip install --upgrade kernels
```

> Prefer to stay outside the subshell? Prefix the commands with `poetry run`
> (for example, `poetry run python -m pip …`).
> If pip stalls on large wheels, `poetry run uv pip …` also works.

CPU-only fallback (fixes `torchvision::nms` mismatch errors):
```bash
poetry run uv pip install --upgrade --force-reinstall --index-url https://download.pytorch.org/whl/cpu torch==2.7.1+cpu torchvision==0.22.1+cpu torchaudio==2.7.1+cpu
```

Verify that PyTorch now sees your GPU:

```bash
poetry run python - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    idx = torch.cuda.current_device()
    print("device:", torch.cuda.get_device_name(idx))
PY
```

Swap `cu128`/`2.7.1` for the wheel that matches your CUDA runtime (e.g., `cu121`
on older drivers). When the backend restarts, the Settings → Inference Device
panel will show a CUDA badge so you can confirm the runtime is ready.

### 2. Configure Environment Variables

Create a `.env` file in the project root by copying the example file:

```bash
cp .env.example .env
```

The backend always loads the repo-root `.env` (even if you start the API from inside `backend/`), and the Settings UI persists changes back to the same file.

If you want to keep secrets completely out of the repository tree, set `FLOAT_ENV_FILE` to an absolute path (or a repo-relative path) before starting the backend. Example (Windows):

```powershell
$env:FLOAT_ENV_FILE="$env:USERPROFILE\\.config\\float\\.env"
```

> Note: legacy installs may have a `backend/.env` created when running from that working directory. The backend loads it as a non-overriding fallback when `FLOAT_ENV_FILE` is unset so you can migrate by saving settings once, then deleting `backend/.env`.

Open the `.env` file and add your OpenAI API key and any other necessary configurations. The most important variables are:

```env
# Your OpenAI API Key
OPENAI_API_KEY=your_openai_api_key

# The default model to use for the API mode
OPENAI_MODEL=gpt-5

# The URL for a local LLM (e.g., Ollama)
LOCAL_LLM_URL=http://localhost:11434
```

Optional Hugging Face settings (backend defaults transfer on):

```env
# Speed up large downloads via the hf-transfer native lib.
# The backend sets this to 1 by default; set to 0 to disable.
# HF_HUB_ENABLE_HF_TRANSFER=0

# Use an existing HF cache location (backend also auto-detects ~/.cache/huggingface/hub)
# HF_HOME=C:\\Users\\<you>\\.cache\\huggingface

# Authenticate for private or gated repos (alternatively: `huggingface-cli login`)
# HF_TOKEN=hf_xxx
```

### Vector Store Persistence

- `FLOAT_RAG_BACKEND` defaults to `chroma` for the local profile. Set it to
  `weaviate` (with matching services) or `memory` to change the vector backend.
- When `chroma` is active, the store persists to
  `CHROMA_PERSIST_DIR` (or `FLOAT_CHROMA_PATH`) and defaults to
  `data/databases/chroma/`. The backend creates the directory on start if it is
  missing.
- The **Settings → Service status** panel now includes a *Vector store* card that
  reads `/api/rag/status` and surfaces the configured backend, persistence path,
  document count, file footprint, and last update time. Use it to confirm the
  directory is writable when relocating the store to shared or external disks.
- The same payload also carries Celery heartbeat data, so the UI can show worker
  counts alongside the storage diagnostics without extra network probes.

Notes on the recent Hugging Face XET update:
- The Hub now uses XET for large file storage. With `hf-xet` installed, you do
  not need git‑lfs locally.
- We rely on `snapshot_download(repo_id=..., local_dir=...)`. The parameters
  `local_dir_use_symlinks` and `resume_download` were removed to match updated
  hub semantics and avoid deprecation warnings.
- The Settings UI calls backend endpoints for model size and download. If a
  model mapping is a placeholder or a repo is invalid/private, the UI will show
  size `--` and downloads will return clear 400/403/404 errors instead of
  breaking Settings.

### Vision Captioning (Optional)

- Default captioner is Google PaliGemma2 small: `google/paligemma2-3b-pt-224`.
- Set `VISION_CAPTION_MODEL` to override (local path or HF repo id).
- To avoid network downloads, prefetch the model using the backend’s download API:
  - `POST /api/models/jobs {"model":"paligemma2-3b-pt-224"}`
  - `GET /api/models/jobs/{id}` until `status` is `completed`
  - `GET /api/models/verify/paligemma2-3b-pt-224` to confirm
- If the model is gated, set `HUGGINGFACE_HUB_TOKEN` (or `HF_TOKEN`).

The captioner gracefully falls back to a placeholder caption when the model or heavy deps are unavailable, keeping tests and light setups fast.

All available settings can be configured from the **Settings** page in the UI once the application is running.

### 3. Launch the Backend

Activate the virtual environment created by Poetry and launch the application.

```bash
# Activate the environment
poetry shell

# Launch the backend server (typically on http://localhost:8000)
uvicorn app.main:app --reload
```

You can also use the project's launcher script, which handles both backend and frontend:
```bash
# Start both backend and frontend with automatic port selection
poetry run float
```

---

## Frontend Setup

The frontend is a React application built with Vite.

### 1. Install Dependencies

Navigate to the `frontend` directory and install the required npm packages.

```bash
cd frontend
npm install
```
If you encounter errors later about `vite` not being recognized, it means this step was not completed successfully.

### 2. Start the Frontend Development Server

Once the dependencies are installed, you can start the Vite development server.

```bash
# This command must be run from inside the 'frontend' directory
npm run dev
```

The frontend will be available at `http://localhost:5173` (or the next available port) and will proxy API requests to the backend.

---

## Docker Deployment (Optional)

A Dockerfile is provided for containerizing the backend application.

### Build and Run the Backend Image

```bash
# Build the image from the project root
docker build -f docker/backend.Dockerfile -t float-backend .

# Run the container
docker run -p 8000:8000 --env-file .env float-backend
```

For a full multi-container setup including the frontend, Redis, and PostgreSQL, you can use the `docker-compose.yml` file:
```bash
docker-compose up --build
```

---

## Weaviate (RAG)

RAG features use a Weaviate vector store.

- The Python client (`weaviate-client`) is installed by `poetry install`.
- A running Weaviate instance is required for persistent knowledge features.
- Start via Docker Compose (local dev):
  - `docker compose up -d weaviate`
- Configure URL via env:
  - `WEAVIATE_URL` or `FLOAT_WEAVIATE_URL` (default `http://localhost:8080`).
- Optional auto-start for local dev:
  - Set `FLOAT_AUTO_START_WEAVIATE=true` and the backend will attempt to start
    the `weaviate` service via Docker Compose the first time it needs it.

Settings integration:
- The Settings page can check connectivity via `GET /api/weaviate/status`.
- A “Start Weaviate” button can call `POST /api/weaviate/start` to launch the
  container (best-effort) and report readiness.
- If Weaviate is not reachable and auto-start is disabled or fails, knowledge
  endpoints fall back to a non-persistent in-memory stub suitable for tests.

API quick reference:
- `GET /api/weaviate/status?url=<optional>` → `{ url, reachable }`
- `POST /api/weaviate/start` with body `{ "url"?: string, "wait_seconds"?: number }`
  → `{ url, started, reachable }`

## See Also

- [Architecture Map](architecture_map.md): Detailed codebase structure and request flow.
- [README.md](../README.md): High-level overview and project goals.
