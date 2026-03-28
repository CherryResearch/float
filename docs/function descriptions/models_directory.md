# Models Directory Structure

Model assets can live in several places. When searching for models the backend
looks in the following locations *in order* and aggregates any models it finds:

1. A custom models directory defined in the application settings.
2. The repository's bundled ``data/models`` directory. This is the default location for downloads (legacy ``models`` folders are still detected for backward compatibility).
3. The directory specified by the ``HF_HOME`` environment variable (the
   Hugging Face hub stores models under ``$HF_HOME/hub``).
4. A standard Hugging Face cache, typically located at
   ``~/.cache/huggingface/hub`` (or ``%USERPROFILE%\.cache\huggingface\hub``
   on Windows).

This allows combining models from multiple locations. A common pattern is
keeping the Hugging Face cache for downloaded Transformers checkpoints while
also pointing a managed runtime such as LM Studio at a separate directory that
contains GGUF weights.

The `data/models/` directory is organised by modality:

```
data/models/
  language/
  audio/
    asr/            # Automatic Speech Recognition models
    tts/            # Text-to-Speech models
  image/
    vlm/            # Vision-Language models
  embeddings/
    clip/           # CLIP embedding models
```

The frontend assumes models are available under `data/models` by default. You can
override this by updating `MODELS_BASE_PATH` in `frontend/src/config.js` (defaults to `data/models`) or by using the Custom Models Folder setting in the UI. Models in the huggingface cache
need to be limited to that modality's settings options. Some might cover multiple 
bases, like vision-language compared to clip. 

On the **Settings** page, the model selectors should behave predictably:
- Show curated presets first (e.g., `gpt-5.1` API, `gpt-oss-20b` local, plus Qwen3/Llama 3.1/Gemma/Mistral/Gemini). Presets stay even if not installed and appear grayed out until reachable/downloaded.
- Then show discovered models from `data/models/` and the selected custom folder, filtered by modality to avoid unrelated Hugging Face cache noise (tiny utility models should not flood the list).
- Hugging Face cache entries should be surfaced only when they match the selector’s modality; remaining cache clutter is a known issue.
- A button beside the drop-down allows download/delete of the selected model and displays the size as reported by Hugging Face. Selecting the folder icon opens the folder it's found in. The size of the model as downloaded and as expected from HF are displayed as well as a link to the model page.

> **MXFP4 GPU requirement**  
> Hugging Face snapshots for `gpt-oss-20b` default to the MXFP4 quantised
> weights. These checkpoints only load when a CUDA-capable GPU is available
> through a CUDA-enabled PyTorch build. On CPU-only machines, Float now aborts
> the load early with guidance. Download the `original` (FP16) variant and point
> Float at that subfolder if you need to run the model on CPU instead.

## Advanced local inference tuning
- **Device map strategy**: choose between `auto`, the `balanced*` strategies, or explicit targets such as `cuda:0`/`cpu` so Accelerate can shard massive checkpoints across GPU and host memory.
- **GPU memory budget**: set the fraction of VRAM Float should consume and reserve a safety margin in megabytes; the backend translates this into `max_memory` hints for `transformers`.
- **CPU offload envelope**: cap or expand how much system RAM is used when spillover weights cannot remain on the GPU.
- **Flash attention + attention backends**: opt into Flash Attention when the runtime supports it, or pin `sdpa`/`eager` backends if compatibility issues arise.
- **KV cache controls**: pick cache implementations (`static`, `hybrid`, `offloaded`, `quantized`), specify quantisation backends (`quanto`, `HQQ`), and override dtype/device preferences for the cache.
- **Weight dtype & threading**: optionally force the torch dtype used for model weights and limit CPU thread pools to keep large loads predictable.

Hugging Face (XET) notes:
- The Hugging Face Hub now uses XET for large files.
- For faster downloads, you can set `HF_HUB_ENABLE_HF_TRANSFER=1`.

## Workflow model assignment UI
- Settings → Models groups workflows on the left and model selectors on the right.
- Each workflow row includes:
  - **API model selector**: shows suggested remote models first, then discovered ones. Availability badges: green (reachable), amber (auth/config issue), red (offline), grey (not installed).
  - **Local model selector**: lists local weights from the aggregated directories. Displays size, quantisation, last verified checksum.
  - **Status pill**: summarises `API`, `Local`, `Float server`. Pill turns green when all selected endpoints are reachable. Data flows from `/api/workflows/{id}/status`.
  - **Test button**: runs a quick health ping via `POST /api/workflows/{id}/test` and surfaces latency results.
- Users can pin a single “global default” model set. Pins persist in `settings/workflows.json` and preselect the default when creating new roles.
- When a model is missing, the selector offers a `Download` action invoking `/api/models/download` with progress streamed over `/api/stream/settings`.

## Managed local runtime providers (LM Studio + Ollama)

Float no longer embeds `llama.cpp` as an internal runtime path.

- Local selector includes provider markers: `local/lmstudio` and `local/ollama`.
- Choosing those markers routes inference through OpenAI-compatible server transport (`/v1`) after provider resolution.
- Runtime panel is authoritative for provider lifecycle controls:
  - status (`installed`, `server_running`, `model_loaded`, `loaded_model`, `context_length`)
  - start/stop server (local-managed only)
  - model list + load/unload
  - logs stream
- In `remote-unmanaged` mode, Float does not spawn remote processes and only uses HTTP status/control calls.

MXFP4/BF16 fallback guidance:

- If local Transformers fails on BF16/MXFP4/CUDA incompatibilities (common on some `gpt-oss-20b` setups), users should switch to `local/lmstudio` or `local/ollama` managed quantized runtime.

## Backend compatibility guidance

- Direct local Transformers loading expects a Hugging Face-style checkpoint directory, not a raw `.gguf` file.
- If the weights you have are GGUF, run them through `local/lmstudio`, `local/ollama`, or another OpenAI-compatible server and then point Float at that runtime.
- `Server/LAN` is the right fit when a separate machine or process already serves the model over an OpenAI-compatible HTTP API. Float does not inspect or convert the backend model format there.
- Keep the selected backend aligned with the actual asset format:
  - Hugging Face checkpoint folders -> direct local Transformers path.
  - GGUF weights -> managed provider or server transport.
  - Hosted API models -> `Cloud API`.

## Float server roadmap
- Future releases allow pairing with a personal Float server that hosts shared models. Settings will show connected devices, health, and bandwidth.
- Model selectors treat Float server resources as an additional provider option with status integrated into the single pill.
- Authentication relies on device keys issued via `/api/devices` and proof-of-possession tokens.
