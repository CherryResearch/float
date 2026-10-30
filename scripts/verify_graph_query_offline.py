"""Run only the selected local tests with isolated data and blocked network."""
import argparse
import os
import socket
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", type=Path, required=True)
arguments, test_args = parser.parse_known_args()
repo = arguments.repo.resolve()
if not any(
    not arg.startswith("-") and (repo / arg.split("::", 1)[0]).exists()
    for arg in test_args
):
    parser.error(
        "provide the focused pytest paths to run; the full suite is not selected implicitly"
    )
if not (repo / "backend/app/utils/graph_store.py").is_file():
    parser.error("--repo must name the source checkout")
attempts = []


def denied(*args, **kwargs):
    attempts.append("blocked")
    raise AssertionError("Offline verification does not allow network connections")


socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
with tempfile.TemporaryDirectory(prefix="float-query-verification-") as directory:
    root = Path(directory)
    os.environ.update(
        {
            "FLOAT_ENV_FILE": str(root / "unused.env"),
            "FLOAT_DATA_DIR": str(root / "data"),
            "FLOAT_MEMORY_FILE": str(root / "memory.sqlite3"),
            "FLOAT_CONV_DIR": str(root / "conversations"),
            "FLOAT_CHATGPT_CONFIG_DIR": str(root / "unused-auth"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "GOOGLE_API_KEY": "",
        }
    )
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(repo / "backend"))
    os.chdir(repo)
    import pytest

    code = pytest.main(test_args)
    print("NETWORK_CONNECTION_ATTEMPTS", len(attempts))
    sys.exit(code or (1 if attempts else 0))
