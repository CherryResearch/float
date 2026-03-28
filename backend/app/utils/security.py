import hashlib
import hmac
import json
import os
import re
from typing import Any, Dict

SECRET_KEY = os.getenv("TOOL_SECRET", "dev-secret")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_COMMAND_SUBSTITUTION = re.compile(r"\$\([^)]*\)")
_SHELL_KEYWORDS = (
    "apt",
    "apt-get",
    "bash",
    "brew",
    "chmod",
    "chown",
    "cmd",
    "curl",
    "dd",
    "del",
    "dnf",
    "erase",
    "grep",
    "mkfs",
    "netcat",
    "nc",
    "node",
    "npm",
    "npx",
    "perl",
    "pip",
    "pip3",
    "powershell",
    "python",
    "python3",
    "rm",
    "rmdir",
    "scp",
    "service",
    "sh",
    "ssh",
    "sudo",
    "systemctl",
    "tar",
    "wget",
    "yum",
)
_KEYWORD_PATTERN = "|".join(re.escape(keyword) for keyword in _SHELL_KEYWORDS)
_SHELL_CHAIN_PATTERN = re.compile(
    rf"(?i)(?:;|&&|\|\|)\s*(?:{_KEYWORD_PATTERN})\b"
)
_PIPE_PATTERN = re.compile(rf"(?i)\|\s*(?:{_KEYWORD_PATTERN})\b")


def sanitize_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """Validate tool-call arguments.

    Raises:
        ValueError: if an argument looks like a shell-injection payload.
    """
    def _sanitize(value: Any, path: str) -> Any:
        if isinstance(value, str):
            if _looks_like_shell_payload(value):
                raise ValueError(f"Unsafe characters in argument '{path}'")
            return value
        if isinstance(value, dict):
            return {k: _sanitize(v, f"{path}.{k}" if path else k) for k, v in value.items()}
        if isinstance(value, list):
            return [_sanitize(item, path) for item in value]
        return value

    return {key: _sanitize(val, key) for key, val in args.items()}


def _looks_like_shell_payload(value: str) -> bool:
    if _CONTROL_CHARS.search(value):
        return True
    if _COMMAND_SUBSTITUTION.search(value):
        return True
    if _SHELL_CHAIN_PATTERN.search(value):
        return True
    if _PIPE_PATTERN.search(value):
        return True
    return False


def generate_signature(user: str, tool: str, args: Dict[str, Any]) -> str:
    """Generate an HMAC signature for a tool invocation."""
    payload = json.dumps(
        {"user": user, "tool": tool, "args": args}, sort_keys=True
    ).encode()
    return hmac.new(SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def verify_signature(
    signature: str | None, user: str, tool: str, args: Dict[str, Any]
) -> None:
    """Verify that ``signature`` matches the expected HMAC value."""
    expected = generate_signature(user, tool, args)
    if not signature or not hmac.compare_digest(signature, expected):
        raise PermissionError("Invalid signature")
