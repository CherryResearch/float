from __future__ import annotations

import platform
import subprocess
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - optional dependency
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

try:  # pragma: no cover - torch may be absent in CI
    import torch
except Exception:  # pragma: no cover
    torch = None  # type: ignore


def _format_float(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except Exception:
        return None


def _detect_cuda_devices() -> List[Dict[str, Any]]:
    devices: List[Dict[str, Any]] = []
    if not torch or not torch.cuda.is_available():
        devices.extend(_detect_cuda_via_nvidia_smi())
        return devices
    count = torch.cuda.device_count()
    for idx in range(count):
        try:
            props = torch.cuda.get_device_properties(idx)
        except Exception:
            continue
        total_mem_bytes = getattr(props, "total_memory", None)
        mem_gb = (
            float(total_mem_bytes) / (1024**3)
            if isinstance(total_mem_bytes, (int, float))
            else None
        )
        capability: str | None = None
        try:
            capability = f"{props.major}.{props.minor}"
        except Exception:
            capability = None
        devices.append(
            {
                "id": f"cuda:{idx}",
                "type": "cuda",
                "index": idx,
                "name": getattr(props, "name", f"CUDA Device {idx}"),
                "total_memory": total_mem_bytes,
                "total_memory_gb": _format_float(mem_gb),
                "capability": capability,
            }
        )
    return devices


def _detect_cuda_via_nvidia_smi() -> List[Dict[str, Any]]:
    devices: List[Dict[str, Any]] = []
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return devices
    output = proc.stdout.strip().splitlines()
    for idx, line in enumerate(output):
        parts = [part.strip() for part in line.split(",") if part.strip()]
        if not parts:
            continue
        name = parts[0]
        mem_gb = None
        if len(parts) > 1:
            try:
                mem_gb = float(parts[1])
            except Exception:
                mem_gb = None
        devices.append(
            {
                "id": f"cuda:{idx}",
                "type": "cuda",
                "index": idx,
                "name": name or f"CUDA Device {idx}",
                "total_memory": None,
                "total_memory_gb": _format_float(mem_gb),
                "capability": None,
            }
        )
    return devices


def _detect_mps_devices() -> List[Dict[str, Any]]:
    if not torch:
        return []
    try:
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return [
                {
                    "id": "mps:0",
                    "type": "mps",
                    "index": 0,
                    "name": "Apple MPS",
                    "total_memory": None,
                    "total_memory_gb": None,
                    "capability": None,
                }
            ]
    except Exception:
        return []
    return []


def detect_compute_devices() -> List[Dict[str, Any]]:
    """Return a list of compute devices available for inference."""

    devices: List[Dict[str, Any]] = []
    devices.extend(_detect_cuda_devices())
    devices.extend(_detect_mps_devices())

    cpu_name = platform.processor() or platform.machine() or "CPU"
    devices.append(
        {
            "id": "cpu",
            "type": "cpu",
            "index": 0,
            "name": cpu_name.strip(),
            "total_memory": None,
            "total_memory_gb": None,
            "capability": None,
        }
    )
    return devices


def pick_default_device(devices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the preferred device entry."""

    if not devices:
        return {
            "id": "cpu",
            "type": "cpu",
            "index": 0,
            "name": platform.processor() or "CPU",
            "total_memory": None,
            "total_memory_gb": None,
            "capability": None,
        }
    priority = {"cuda": 0, "mps": 1, "cpu": 2}

    def sort_key(device: Dict[str, Any]):
        device_type = device.get("type", "cpu")
        total_gb = device.get("total_memory_gb") or 0
        return (priority.get(device_type, 3), -float(total_gb))

    sorted_devices = sorted(devices, key=sort_key)
    return sorted_devices[0]


def torch_cuda_diagnostics(devices: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Return a summary describing Torch CUDA support and detected GPUs."""

    info: Dict[str, Any] = {
        "torch_present": bool(torch),
        "torch_version": getattr(torch, "__version__", None) if torch else None,
        "cuda_available": bool(torch and torch.cuda.is_available()),
        "cuda_runtime_version": None,
        "cuda_device_count": 0,
        "detected_device_count": 0,
        "detected_device_names": [],
        "status": "offline",
        "note": None,
    }

    gpu_devices = [
        dev for dev in (devices or []) if isinstance(dev, dict) and dev.get("type") == "cuda"
    ]
    info["detected_device_count"] = len(gpu_devices)
    info["detected_device_names"] = [
        str(dev.get("name") or dev.get("id") or f"cuda:{idx}")
        for idx, dev in enumerate(gpu_devices)
    ]

    if not torch:
        info["note"] = "PyTorch is not installed in this environment."
        return info

    try:
        info["cuda_runtime_version"] = getattr(torch.version, "cuda", None)
    except Exception:
        info["cuda_runtime_version"] = None

    if info["cuda_available"]:
        try:
            count = torch.cuda.device_count()
        except Exception:
            count = 0
        info["cuda_device_count"] = count
        names: List[str] = []
        for idx in range(count):
            try:
                names.append(torch.cuda.get_device_name(idx))
            except Exception:
                names.append(f"cuda:{idx}")
        if names:
            info["detected_device_names"] = names
        info["status"] = "online"
        return info

    # CUDA unavailable
    info["status"] = "degraded" if gpu_devices else "offline"
    if gpu_devices:
        info["note"] = (
            "GPU hardware detected, but the current PyTorch build does not expose CUDA. "
            "Install a CUDA-enabled PyTorch wheel."
        )
    else:
        info["note"] = "No CUDA-capable device detected."
    return info


def _gpu_memory_snapshot_via_nvidia_smi() -> List[Dict[str, Any]]:
    devices: List[Dict[str, Any]] = []
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return devices
    for line in proc.stdout.strip().splitlines():
        parts = [part.strip() for part in line.split(",") if part.strip()]
        if len(parts) < 4:
            continue
        try:
            idx = int(parts[0])
        except (TypeError, ValueError):
            continue
        name = parts[1] or f"CUDA Device {idx}"
        try:
            total_mb = float(parts[2])
            free_mb = float(parts[3])
        except (TypeError, ValueError):
            total_mb = free_mb = None
        total_bytes = int(total_mb * (1024**2)) if total_mb is not None else None
        free_bytes = int(free_mb * (1024**2)) if free_mb is not None else None
        used_bytes = (
            total_bytes - free_bytes if total_bytes is not None and free_bytes is not None else None
        )
        devices.append(
            {
                "id": f"cuda:{idx}",
                "index": idx,
                "name": name,
                "total_bytes": total_bytes,
                "free_bytes": free_bytes,
                "used_bytes": used_bytes,
                "allocated_bytes": None,
                "reserved_bytes": None,
            }
        )
    return devices


def gpu_memory_snapshot(*, use_torch: bool = True) -> List[Dict[str, Any]]:
    """Return runtime GPU memory statistics (bytes) for each CUDA device."""

    snapshots: List[Dict[str, Any]] = []
    if use_torch and torch and torch.cuda.is_available():
        try:
            count = torch.cuda.device_count()
        except Exception:
            count = 0
        for idx in range(count):
            name: str | None
            try:
                name = torch.cuda.get_device_name(idx)
            except Exception:
                name = None
            free_bytes = total_bytes = allocated_bytes = reserved_bytes = used_bytes = None
            try:
                with torch.cuda.device(idx):
                    free_bytes, total_bytes = torch.cuda.mem_get_info()  # type: ignore[attr-defined]
                    allocated_bytes = torch.cuda.memory_allocated()
                    reserved_bytes = torch.cuda.memory_reserved()
            except Exception:
                pass
            if total_bytes is not None and free_bytes is not None:
                used_bytes = total_bytes - free_bytes
            snapshots.append(
                {
                    "id": f"cuda:{idx}",
                    "index": idx,
                    "name": name or f"CUDA Device {idx}",
                    "total_bytes": total_bytes,
                    "free_bytes": free_bytes,
                    "used_bytes": used_bytes,
                    "allocated_bytes": allocated_bytes,
                    "reserved_bytes": reserved_bytes,
                }
            )
        return snapshots
    return _gpu_memory_snapshot_via_nvidia_smi()


def system_memory_snapshot() -> Dict[str, Any]:
    """Return system RAM availability in bytes."""

    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            return {
                "total_bytes": int(vm.total),
                "available_bytes": int(vm.available),
                "used_bytes": int(vm.used),
                "percent": float(vm.percent),
            }
        except Exception:
            pass
    return {}
