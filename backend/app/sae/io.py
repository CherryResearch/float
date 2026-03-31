from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import ActivationRecord, SAEWeights


def _ensure_parent(path: Path) -> None:
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


def save_activation_record(record: ActivationRecord, path: str | Path) -> Path:
    target = Path(path)
    extension = target.suffix.lower()
    if extension in ("", ".json"):
        _ensure_parent(target)
        target.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        return target
    if extension == ".npz":
        return _save_activation_npz(record, target)
    if extension == ".parquet":
        return _save_activation_parquet(record, target)
    raise ValueError(f"Unsupported activation format: {target.suffix}")


def load_activation_record(path: str | Path) -> ActivationRecord:
    target = Path(path)
    extension = target.suffix.lower()
    if extension in ("", ".json"):
        payload = json.loads(target.read_text(encoding="utf-8"))
        return ActivationRecord.from_dict(payload)
    if extension == ".npz":
        return _load_activation_npz(target)
    if extension == ".parquet":
        return _load_activation_parquet(target)
    raise ValueError(f"Unsupported activation format: {target.suffix}")


def save_sae_weights(weights: SAEWeights, path: str | Path) -> Path:
    target = Path(path)
    _ensure_parent(target)
    target.write_text(json.dumps(weights.to_dict(), indent=2), encoding="utf-8")
    return target


def load_sae_weights(path: str | Path) -> SAEWeights:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return SAEWeights.from_dict(payload)


def _require_numpy() -> Any:
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - optional runtime path
        raise RuntimeError(
            "NumPy is required for NPZ activation format. "
            "Install it first or use JSON."
        ) from exc
    return np


def _save_activation_npz(record: ActivationRecord, path: Path) -> Path:
    np = _require_numpy()
    _ensure_parent(path)
    np.savez(
        path,
        model_name=np.array(record.model_name),
        layer_name=np.array(record.layer_name),
        token_ids=np.array(record.token_ids, dtype=np.int64),
        tokens=np.array(record.tokens, dtype=object),
        activations=np.array(record.activations, dtype=float),
    )
    return path


def _load_activation_npz(path: Path) -> ActivationRecord:
    np = _require_numpy()
    archive = np.load(path, allow_pickle=True)
    token_ids = [int(value) for value in archive["token_ids"].tolist()]
    tokens = [str(value) for value in archive["tokens"].tolist()]
    activations = archive["activations"].tolist()
    return ActivationRecord(
        model_name=str(archive["model_name"].tolist()),
        layer_name=str(archive["layer_name"].tolist()),
        token_ids=token_ids,
        tokens=tokens,
        activations=activations,
    )


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except Exception as exc:  # pragma: no cover - optional runtime path
        raise RuntimeError(
            "pandas + pyarrow are required for Parquet activation format. "
            "Install them first or use JSON."
        ) from exc
    return pd


def _save_activation_parquet(record: ActivationRecord, path: Path) -> Path:
    pd = _require_pandas()
    _ensure_parent(path)
    rows: list[dict[str, Any]] = []
    for row_index, activation in enumerate(record.activations):
        row: dict[str, Any] = {
            "row_index": row_index,
            "token_id": record.token_ids[row_index],
            "token": record.tokens[row_index] if row_index < len(record.tokens) else "",
        }
        row.update({f"d{idx}": float(value) for idx, value in enumerate(activation)})
        rows.append(row)

    frame = pd.DataFrame(rows)
    frame.to_parquet(path, index=False)

    meta_path = path.with_suffix(path.suffix + ".meta.json")
    meta_payload = {
        "format": "activation_record.parquet.v1",
        "model_name": record.model_name,
        "layer_name": record.layer_name,
        "metadata": record.metadata,
    }
    meta_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")
    return path


def _load_activation_parquet(path: Path) -> ActivationRecord:
    pd = _require_pandas()
    frame = pd.read_parquet(path)
    dims = sorted(
        [column for column in frame.columns if column.startswith("d")],
        key=lambda item: int(item[1:]),
    )
    token_ids = [int(value) for value in frame["token_id"].tolist()]
    tokens = [str(value) for value in frame["token"].fillna("").tolist()]
    activations: list[list[float]] = []
    for _, row in frame.iterrows():
        activations.append([float(row[column]) for column in dims])

    meta_path = path.with_suffix(path.suffix + ".meta.json")
    model_name = "unknown"
    layer_name = "unknown"
    metadata: dict[str, Any] = {}
    if meta_path.exists():
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        model_name = str(payload.get("model_name", model_name))
        layer_name = str(payload.get("layer_name", layer_name))
        metadata = dict(payload.get("metadata") or {})

    return ActivationRecord(
        model_name=model_name,
        layer_name=layer_name,
        token_ids=token_ids,
        tokens=tokens,
        activations=activations,
        metadata=metadata,
    )
