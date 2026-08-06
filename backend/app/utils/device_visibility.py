from __future__ import annotations

import ipaddress
import os
import secrets
import socket
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from app.utils import user_settings
from fastapi import Request

_LOOPBACK_ALIASES = {"localhost", "testclient"}
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def is_lan_binding_host(host: str) -> bool:
    normalized = str(host or "").strip().strip("[]").lower()
    return normalized in {"0.0.0.0", "::"} or classify_host(normalized) == "lan"


def _backend_binding_status() -> Dict[str, Any]:
    bind_host = str(os.getenv("FLOAT_BACKEND_HOST") or "").strip()
    normalized = bind_host.strip("[]").lower()
    binding_known = bool(normalized)
    lan_listening = is_lan_binding_host(normalized)
    return {
        "bind_host": bind_host,
        "binding_known": binding_known,
        "lan_listening": lan_listening,
    }


def load_visibility_settings() -> Dict[str, Any]:
    settings = user_settings.load_settings()
    binding = _backend_binding_status()
    return {
        "lan_visible": bool(settings.get("sync_visible_on_lan")),
        "online_visible": bool(settings.get("sync_visible_online")),
        "online_url": str(settings.get("sync_online_url") or "").strip(),
        "online_supported": False,
        **binding,
    }


def _request_origin(request: Optional[Request]) -> Tuple[str, str]:
    if request is None:
        return ("http", "")
    scheme = (
        str(
            request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
        ).strip()
        or "http"
    )
    host = str(request.headers.get("host") or request.url.netloc or "").strip()
    if not host:
        hostname = str(request.url.hostname or "").strip()
        if hostname:
            if request.url.port:
                host = f"{hostname}:{request.url.port}"
            else:
                host = hostname
    return (scheme, host)


def _split_host_port(host: str) -> Tuple[str, Optional[int]]:
    raw = str(host or "").strip()
    if not raw:
        return ("", None)
    parsed = urlsplit(f"//{raw}")
    return (str(parsed.hostname or "").strip(), parsed.port)


def _format_origin(scheme: str, host: str, port: Optional[int]) -> str:
    hostname = str(host or "").strip()
    if not hostname:
        return ""
    display_host = hostname
    if ":" in hostname and not hostname.startswith("["):
        display_host = f"[{hostname}]"
    if port and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        return f"{scheme}://{display_host}:{port}"
    return f"{scheme}://{display_host}"


def client_host(request: Optional[Request]) -> str:
    if request is None:
        return ""
    client = getattr(request, "client", None)
    direct = str(getattr(client, "host", "") or "").strip()
    # Only a same-host proxy may tell Float about the original client. A direct
    # LAN caller can otherwise spoof X-Forwarded-For: 127.0.0.1 and bypass the
    # visibility boundary.
    if classify_host(direct) == "loopback":
        forwarded = (
            str(request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
        )
        if forwarded:
            return forwarded
    return direct


def is_trusted_frontend_proxy(request: Optional[Request]) -> bool:
    if request is None:
        return False
    client = getattr(request, "client", None)
    direct = str(getattr(client, "host", "") or "").strip()
    marker_present = (
        str(request.headers.get("x-float-frontend-proxy") or "").strip() == "1"
    )
    if not marker_present:
        return False
    if classify_host(direct) == "loopback":
        return True
    expected_token = str(os.getenv("FLOAT_FRONTEND_PROXY_TOKEN") or "").strip()
    supplied_token = str(
        request.headers.get("x-float-frontend-proxy-token") or ""
    ).strip()
    return bool(
        expected_token
        and supplied_token
        and secrets.compare_digest(expected_token, supplied_token)
    )


def classify_host(host: str) -> str:
    raw = str(host or "").strip().lower()
    if not raw:
        return "unknown"
    if raw in _LOOPBACK_ALIASES:
        return "loopback"
    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError:
        if raw.endswith(".local"):
            return "lan"
        return "unknown"
    if parsed.is_loopback:
        return "loopback"
    if parsed in _TAILSCALE_CGNAT:
        return "lan"
    if parsed.is_private or parsed.is_link_local:
        return "lan"
    return "public"


def device_access_rejection_detail(request: Optional[Request]) -> Optional[str]:
    scope = classify_host(client_host(request))
    settings = load_visibility_settings()
    if scope == "public":
        return "Float device access is not exposed online yet."
    if scope == "lan" and not settings["lan_visible"]:
        return "LAN visibility is turned off for this device."
    return None


def _detect_lan_ips() -> List[str]:
    candidates: List[str] = []

    def add_candidate(value: str) -> None:
        ip = str(value or "").strip()
        if not ip or ip in candidates:
            return
        if classify_host(ip) == "lan":
            candidates.append(ip)

    try:
        add_candidate(socket.gethostbyname(socket.gethostname()))
    except Exception:
        pass
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
        for info in infos:
            if len(info) >= 5 and info[4]:
                add_candidate(str(info[4][0]))
    except Exception:
        pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("10.255.255.255", 1))
        add_candidate(str(sock.getsockname()[0]))
        sock.close()
    except Exception:
        pass
    return candidates


def _detect_lan_ip() -> str:
    candidates = _detect_lan_ips()
    return candidates[0] if candidates else ""


def _resolve_ipv4_addresses(host: str) -> List[str]:
    resolved: List[str] = []
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_INET)
    except Exception:
        return resolved
    for info in infos:
        if len(info) < 5 or not info[4]:
            continue
        ip = str(info[4][0] or "").strip()
        if ip and ip not in resolved:
            resolved.append(ip)
    return resolved


def _preferred_lan_host(origin_host: str) -> str:
    origin = str(origin_host or "").strip()
    if classify_host(origin) == "lan":
        return origin
    lan_ips = _detect_lan_ips()
    if not lan_ips:
        return ""
    hostname = str(socket.gethostname() or "").strip()
    hostname_candidates: List[str] = []
    for candidate in (
        f"{hostname.lower()}.local",
        f"{hostname}.local",
        hostname.lower(),
        hostname,
    ):
        cleaned = str(candidate or "").strip()
        if (
            not cleaned
            or cleaned in hostname_candidates
            or classify_host(cleaned) == "loopback"
        ):
            continue
        hostname_candidates.append(cleaned)
    for candidate in hostname_candidates:
        resolved = _resolve_ipv4_addresses(candidate)
        if any(ip in lan_ips for ip in resolved):
            return candidate
    return lan_ips[0]


def advertised_device_access(request: Optional[Request]) -> Dict[str, Any]:
    settings = load_visibility_settings()
    scheme, origin = _request_origin(request)
    origin_host, origin_port = _split_host_port(origin)
    bound_host = str(settings.get("bind_host") or "").strip().strip("[]")
    bound_host_normalized = bound_host.lower()
    lan_host = (
        bound_host
        if bound_host_normalized not in {"0.0.0.0", "::"}
        and classify_host(bound_host) == "lan"
        else _preferred_lan_host(origin_host)
    )
    local_url = _format_origin(scheme, "127.0.0.1", origin_port)
    candidate_lan_url = _format_origin(scheme, lan_host, origin_port)
    lan_url = (
        candidate_lan_url
        if settings["lan_visible"] and settings["lan_listening"]
        else ""
    )
    online_url = ""
    online_status = "coming_soon"
    if settings["online_visible"] and settings["online_url"]:
        online_url = settings["online_url"]
        online_status = "configured_but_disabled"
    return {
        "request_scope": classify_host(client_host(request)),
        "visibility": {
            "lan_enabled": settings["lan_visible"],
            "lan_listening": settings["lan_listening"],
            "lan_binding_known": settings["binding_known"],
            "lan_bind_host": settings["bind_host"],
            "lan_state": (
                "listening"
                if settings["lan_visible"] and settings["lan_listening"]
                else "restart_required"
                if settings["lan_visible"]
                else "hidden"
            ),
            "online_enabled": settings["online_visible"],
            "online_supported": settings["online_supported"],
        },
        "advertised_urls": {
            "local": local_url,
            "lan": lan_url,
            "lan_candidate": candidate_lan_url,
            "internet": online_url,
        },
        "internet_status": online_status,
    }


def candidate_device_urls(request: Optional[Request]) -> List[str]:
    access = advertised_device_access(request)
    urls: List[str] = []
    lan_url = str(access.get("advertised_urls", {}).get("lan") or "").strip()
    lan_enabled = bool(access.get("visibility", {}).get("lan_enabled"))
    if lan_enabled and lan_url:
        urls.append(lan_url)
    local_url = str(access.get("advertised_urls", {}).get("local") or "").strip()
    if local_url and not urls:
        urls.append(local_url)
    return urls
