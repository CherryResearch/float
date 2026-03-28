"""Utilities for interacting with a Weaviate instance.

This module provides helper functions to create a Weaviate client and to
migrate data from an existing Chroma collection into Weaviate.

It also includes an optional "auto-start" convenience that, when enabled via
``FLOAT_AUTO_START_WEAVIATE=true`` (or ``1``), attempts to bring up the
``weaviate`` service using ``docker compose`` if the target endpoint is not
reachable. This is intended for local development only; production deployments
should provision Weaviate explicitly.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHROMA_DIR = (REPO_ROOT / "data" / "databases" / "chroma").resolve()
DEFAULT_WEAVIATE_GRPC_PORT = 50051


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _v4_collection_exists(client, collection_name: str) -> bool:
    collections = getattr(client, "collections", None)
    if collections is None:
        return False
    exists = getattr(collections, "exists", None)
    if callable(exists):
        return bool(exists(collection_name))
    list_all = getattr(collections, "list_all", None)
    if not callable(list_all):
        return False
    listed = list_all()
    if isinstance(listed, dict):
        return collection_name in listed
    if isinstance(listed, list):
        for entry in listed:
            if entry == collection_name:
                return True
            if isinstance(entry, dict) and entry.get("name") == collection_name:
                return True
            if getattr(entry, "name", None) == collection_name:
                return True
    return False


def _ensure_v4_collection(client, collection_name: str) -> None:
    collections = getattr(client, "collections", None)
    if collections is None or _v4_collection_exists(client, collection_name):
        return
    schema = {
        "class": collection_name,
        "vectorizer": "none",
        "properties": [
            {"name": "text", "dataType": ["text"]},
            {"name": "metadata", "dataType": ["text"]},
        ],
    }
    create_from_dict = getattr(collections, "create_from_dict", None)
    if callable(create_from_dict):
        create_from_dict(schema)
        return
    create = getattr(collections, "create", None)
    if not callable(create):
        raise RuntimeError("Weaviate v4 client does not expose collection creation")
    try:
        from weaviate.classes.config import Configure, DataType, Property

        create(
            collection_name,
            vector_config=Configure.Vectors.self_provided(),
            properties=[
                Property(name="text", data_type=DataType.TEXT),
                Property(name="metadata", data_type=DataType.TEXT),
            ],
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to create Weaviate collection {collection_name}: {exc}"
        ) from exc


def _find_compose_file(start: Path | None = None) -> Optional[Path]:
    """Return the nearest ``docker-compose.yml`` from ``start`` upward.

    Searches current working directory by default and walks parents up to the
    filesystem root. Returns ``None`` if not found.
    """
    candidates = [
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    ]
    here = start or Path.cwd()
    seen: set[Path] = set()
    while here not in seen:
        seen.add(here)
        for name in candidates:
            p = here / name
            if p.exists():
                return p
        if here.parent == here:
            break
        here = here.parent
    return None


def _looks_ready(url: str, timeout: float = 0.75) -> bool:
    """Return True if a Weaviate endpoint appears ready.

    Uses ``/v1/.well-known/ready`` first, then falls back to ``/v1/meta``.
    The check is best-effort and uses a short timeout to avoid blocking.
    """
    try:
        r = requests.get(url.rstrip("/") + "/v1/.well-known/ready", timeout=timeout)
        if r.status_code == 200:
            return True
    except Exception:
        pass
    try:
        r = requests.get(url.rstrip("/") + "/v1/meta", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _try_autostart(url: str, wait_seconds: int = 45) -> None:
    """Attempt to start Weaviate locally via docker compose (best-effort).

    - No-op if ``FLOAT_AUTO_START_WEAVIATE`` is not truthy.
    - Looks for a compose file in the repo or current working directory.
    - Runs ``docker compose up -d weaviate`` (fall back to ``docker-compose``).
    - Waits up to ``wait_seconds`` for the service to become ready.

    This is a development convenience only; failures are swallowed so callers
    can decide how to proceed (e.g. fall back to an in-memory stub).
    """
    flag = os.getenv("FLOAT_AUTO_START_WEAVIATE", "false").lower()
    if flag not in {"1", "true", "yes", "on"}:
        return

    if _looks_ready(url):
        return

    compose_file = _find_compose_file()
    if not compose_file:
        return

    cmd_variants = [
        ["docker", "compose", "-f", str(compose_file), "up", "-d", "weaviate"],
        ["docker-compose", "-f", str(compose_file), "up", "-d", "weaviate"],
    ]

    for cmd in cmd_variants:
        try:
            subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            break
        except Exception:
            # Try the next variant
            continue

    # Poll readiness with a short backoff window
    deadline = time.time() + max(5, wait_seconds)
    while time.time() < deadline:
        if _looks_ready(url):
            return
        time.sleep(1.0)


def create_client(url: str, api_key: Optional[str] = None):
    """Return an instance of ``weaviate.WeaviateClient``.

    Parameters
    ----------
    url:
        Base URL of the Weaviate instance. The gRPC port is assumed to be the
        HTTP port plus one, matching Weaviate's default.
    api_key:
        Optional API key for authentication.
    """

    from urllib.parse import urlparse

    import weaviate  # Local import to keep dependency optional

    # Best-effort auto-start for local dev if requested
    try:
        _try_autostart(url)
    except Exception:
        # Never fail create_client due to auto-start issues
        pass

    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    secure = parsed.scheme == "https"
    grpc_host = (
        os.getenv("FLOAT_WEAVIATE_GRPC_HOST") or os.getenv("WEAVIATE_GRPC_HOST") or host
    )
    grpc_port = _env_int(
        "FLOAT_WEAVIATE_GRPC_PORT",
        _env_int(
            "WEAVIATE_GRPC_PORT",
            443 if secure and port == 443 else DEFAULT_WEAVIATE_GRPC_PORT,
        ),
    )

    auth = None
    if api_key:
        try:
            from weaviate.classes.init import Auth

            auth = Auth.api_key(api_key)
        except Exception:
            auth_factory = getattr(getattr(weaviate, "auth", None), "AuthApiKey", None)
            auth = auth_factory(api_key) if callable(auth_factory) else None
    # Prefer v4 style connect_to_custom; fall back to v3 Client if needed
    if hasattr(weaviate, "connect_to_custom"):
        return weaviate.connect_to_custom(
            http_host=host,
            http_port=port,
            http_secure=secure,
            grpc_host=grpc_host,
            grpc_port=grpc_port,
            grpc_secure=secure,
            auth_credentials=auth,
            skip_init_checks=True,
        )
    # Legacy API (for older tests/dummies)
    if hasattr(weaviate, "Client"):
        return weaviate.Client(url, auth_client_secret=auth)
    raise RuntimeError("Compatible Weaviate client API not found")


def autostart_weaviate(url: str, wait_seconds: int = 45) -> bool:
    """Public helper to start Weaviate via Docker Compose and wait for readiness.

    Returns True if the endpoint is reachable after the attempt; otherwise False.
    Honours ``FLOAT_AUTO_START_WEAVIATE`` like ``create_client`` but can be
    called explicitly by an admin/UI action regardless of that flag.
    """
    try:
        # Force an attempt regardless of env flag by temporarily setting it
        prev = os.getenv("FLOAT_AUTO_START_WEAVIATE")
        os.environ["FLOAT_AUTO_START_WEAVIATE"] = "true"
        _try_autostart(url, wait_seconds=wait_seconds)
    finally:
        if prev is None:
            os.environ.pop("FLOAT_AUTO_START_WEAVIATE", None)
        else:
            os.environ["FLOAT_AUTO_START_WEAVIATE"] = prev
    return _looks_ready(url)


def export_chroma_to_weaviate(
    collection_name: str,
    class_name: str,
    chroma_persist_dir: str = str(DEFAULT_CHROMA_DIR),
    weaviate_url: str = "http://localhost:8080",
    api_key: Optional[str] = None,
) -> int:
    """Export vectors from Chroma and import them into Weaviate.

    Parameters
    ----------
    collection_name:
        Name of the Chroma collection to export.
    class_name:
        Target Weaviate class name. The class is created if missing.
    chroma_persist_dir:
        Directory where the Chroma persistent data lives.
    weaviate_url:
        Base URL of the destination Weaviate instance.
    api_key:
        Optional API key for Weaviate authentication.

    Returns
    -------
    int
        Number of vectors imported into Weaviate.
    """

    # Local import to avoid hard dependency at module load time
    import chromadb

    client = create_client(weaviate_url, api_key)

    chroma_client = chromadb.PersistentClient(path=chroma_persist_dir)
    collection = chroma_client.get_or_create_collection(collection_name)
    data = collection.get()
    ids = data.get("ids", [])
    docs = data.get("documents", [])
    metas = data.get("metadatas", [])
    embeds = data.get("embeddings", [])

    # Ensure the target class exists in Weaviate
    if hasattr(client, "collections"):
        _ensure_v4_collection(client, class_name)
        collection = client.collections.use(class_name)
        for _id, doc, meta, vec in zip(ids, docs, metas, embeds):
            properties = {
                "text": doc or "",
                "metadata": json.dumps(meta or {}),
            }
            try:
                collection.data.delete_by_id(str(_id))
            except Exception:
                pass
            collection.data.insert(
                properties=properties,
                uuid=str(_id),
                vector=vec,
            )
        return len(ids)

    existing = client.schema.get().get("classes", [])
    if not any(c.get("class") == class_name for c in existing):
        schema = {
            "class": class_name,
            "vectorizer": "none",
            "properties": [
                {"name": "text", "dataType": ["text"]},
                {"name": "metadata", "dataType": ["text"]},
            ],
        }
        client.schema.create_class(schema)

    with client.batch as batch:
        for _id, doc, meta, vec in zip(ids, docs, metas, embeds):
            properties = {
                "text": doc or "",
                "metadata": json.dumps(meta or {}),
            }
            batch.add_data_object(
                properties,
                class_name=class_name,
                vector=vec,
                uuid=_id,
            )

    return len(ids)
