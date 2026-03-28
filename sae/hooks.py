from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import ActivationRecord, SteeringConfig


class RuntimeUnavailableError(RuntimeError):
    """Raised when the selected live runtime cannot provide hook access."""


@dataclass
class HookTarget:
    module: Any
    name: str


def _import_transformers_runtime() -> tuple[Any, Any, Any]:
    try:
        import torch
    except Exception as exc:  # pragma: no cover - optional path
        raise RuntimeUnavailableError(
            "PyTorch is not installed in this environment. "
            "Use --activations for offline mode, or install torch+transformers "
            "for live hook capture."
        ) from exc

    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:  # pragma: no cover - optional path
        raise RuntimeUnavailableError(
            "transformers is not installed in this environment. "
            "Use --activations for offline mode, or install transformers for live capture."
        ) from exc

    return torch, AutoModelForCausalLM, AutoTokenizer


def _resolve_block_list(model: Any) -> tuple[list[Any], str]:
    if hasattr(model, "model") and hasattr(model.model, "layers"):
        return list(model.model.layers), "model.layers"
    if hasattr(model, "transformer") and hasattr(model.transformer, "h"):
        return list(model.transformer.h), "transformer.h"
    if hasattr(model, "gpt_neox") and hasattr(model.gpt_neox, "layers"):
        return list(model.gpt_neox.layers), "gpt_neox.layers"
    raise RuntimeUnavailableError(
        "Unsupported model architecture for automatic layer hook resolution."
    )


def resolve_hook_target(model: Any, layer: int, module_target: str = "resid_post") -> HookTarget:
    blocks, block_path = _resolve_block_list(model)
    if layer < 0 or layer >= len(blocks):
        raise ValueError(f"Layer {layer} is out of range for {block_path} (n={len(blocks)}).")

    block = blocks[layer]
    module_target = module_target.strip().lower()
    if module_target in {"resid", "resid_post", "block_output"}:
        return HookTarget(module=block, name=f"{block_path}[{layer}]")
    if module_target in {"mlp", "mlp_out"} and hasattr(block, "mlp"):
        return HookTarget(module=block.mlp, name=f"{block_path}[{layer}].mlp")

    raise RuntimeUnavailableError(
        f"Unsupported module_target='{module_target}' for this model. "
        "Use resid_post (default) or mlp when available."
    )


def capture_transformers_activations(
    model_name: str,
    prompt: str,
    layer: int,
    module_target: str = "resid_post",
    device: str = "cpu",
    trust_remote_code: bool = False,
) -> ActivationRecord:
    """Capture one layer activation trace via forward hook.

    This path is future-facing for `gpt-oss-20b` (unquantized transformers).
    It can work now on small local Hugging Face checkpoints.
    """

    torch, AutoModelForCausalLM, AutoTokenizer = _import_transformers_runtime()
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    model = AutoModelForCausalLM.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    model.to(device)
    model.eval()

    encoded = tokenizer(prompt, return_tensors="pt")
    encoded = {key: value.to(device) for key, value in encoded.items()}
    target = resolve_hook_target(model, layer=layer, module_target=module_target)

    captured: dict[str, Any] = {}

    def _capture_hook(_: Any, __: Any, output: Any) -> None:
        tensor = output[0] if isinstance(output, tuple) else output
        captured["hidden"] = tensor.detach().cpu()

    handle = target.module.register_forward_hook(_capture_hook)
    try:
        with torch.no_grad():
            model(**encoded)
    finally:
        handle.remove()

    if "hidden" not in captured:
        raise RuntimeUnavailableError("Forward hook did not capture hidden states.")

    hidden = captured["hidden"]
    if hidden.ndim != 3:
        raise RuntimeUnavailableError(
            f"Expected hooked tensor shape [batch, seq, d_model], got {tuple(hidden.shape)}."
        )

    token_ids = encoded["input_ids"][0].detach().cpu().tolist()
    tokens = tokenizer.convert_ids_to_tokens(token_ids)
    activations = hidden[0].tolist()
    return ActivationRecord(
        model_name=model_name,
        layer_name=target.name,
        token_ids=[int(token_id) for token_id in token_ids],
        tokens=[str(token) for token in tokens],
        activations=activations,
        metadata={"module_target": module_target},
    )


def _parse_token_positions(token_positions: str, seq_len: int) -> list[int]:
    mode = token_positions.strip().lower()
    if mode == "all":
        return list(range(seq_len))
    if mode == "last":
        return [seq_len - 1] if seq_len > 0 else []

    positions: list[int] = []
    for chunk in mode.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        index = int(chunk)
        if index < 0:
            index = seq_len + index
        if 0 <= index < seq_len:
            positions.append(index)
    return sorted(set(positions))


def _build_decoder_delta(
    decoder: list[list[float]],
    features: dict[int, float],
) -> list[float]:
    d_model = len(decoder[0]) if decoder else 0
    delta = [0.0] * d_model
    for feature_id, alpha in features.items():
        if feature_id < 0 or feature_id >= len(decoder):
            continue
        row = decoder[feature_id]
        for index, value in enumerate(row):
            delta[index] += float(alpha) * float(value)
    return delta


class TorchSteeringHook:
    """Forward hook that adds SAE decoder directions to the hidden state."""

    def __init__(self, decoder: list[list[float]], config: SteeringConfig):
        self._decoder = decoder
        self._config = config
        self.applied_positions: list[int] = []

    def __call__(self, _: Any, __: Any, output: Any) -> Any:
        if self._config.dry_run:
            return output

        hidden = output[0] if isinstance(output, tuple) else output
        if getattr(hidden, "ndim", None) != 3:
            return output

        seq_len = int(hidden.shape[1])
        positions = _parse_token_positions(self._config.token_positions, seq_len=seq_len)
        self.applied_positions = positions
        if not positions:
            return output

        delta = _build_decoder_delta(self._decoder, self._config.features)
        if not delta:
            return output

        delta_tensor = hidden.new_tensor(delta)
        steered = hidden.clone()
        for position in positions:
            steered[:, position, :] = steered[:, position, :] + delta_tensor

        if isinstance(output, tuple):
            return (steered, *output[1:])
        return steered


def run_transformers_with_steering(
    model_name: str,
    prompt: str,
    layer: int,
    module_target: str,
    config: SteeringConfig,
    decoder: list[list[float]],
    device: str = "cpu",
    max_new_tokens: int = 48,
    trust_remote_code: bool = False,
) -> dict[str, Any]:
    """Run one generation with steering hook attached."""

    torch, AutoModelForCausalLM, AutoTokenizer = _import_transformers_runtime()
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    model = AutoModelForCausalLM.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    model.to(device)
    model.eval()

    target = resolve_hook_target(model, layer=layer, module_target=module_target)
    hook = TorchSteeringHook(decoder=decoder, config=config)

    if config.dry_run:
        return {
            "dry_run": True,
            "model_name": model_name,
            "layer_name": target.name,
            "features": config.features,
            "token_positions": config.token_positions,
        }

    encoded = tokenizer(prompt, return_tensors="pt")
    encoded = {key: value.to(device) for key, value in encoded.items()}

    handle = target.module.register_forward_hook(hook)
    try:
        with torch.no_grad():
            generated_ids = model.generate(**encoded, max_new_tokens=max_new_tokens)
    finally:
        handle.remove()

    text = tokenizer.decode(generated_ids[0], skip_special_tokens=True)
    return {
        "dry_run": False,
        "model_name": model_name,
        "layer_name": target.name,
        "applied_positions": hook.applied_positions,
        "text": text,
    }


def capture_llama_cpp_or_lmstudio_stub(*_: Any, **__: Any) -> ActivationRecord:
    """Option C placeholder.

    TODO(oss-20b-runtime):
    - Add activation extraction bridge for quantized runtimes (LM Studio/llama.cpp).
    - Verify whether per-layer full-sequence activations are exposed or only last-token.
    - Map exposed tensors into ActivationRecord so inspect/train paths stay unchanged.
    """

    raise RuntimeUnavailableError(
        "LLM Studio/llama.cpp activation bridge is not implemented yet. "
        "Use offline activation files for now."
    )
