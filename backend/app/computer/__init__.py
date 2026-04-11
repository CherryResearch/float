import importlib

from .playwright_runtime import PlaywrightComputerRuntime
from .session_store import ComputerSessionStore
from .types import (
    DEFAULT_DISPLAY_HEIGHT,
    DEFAULT_DISPLAY_WIDTH,
    ComputerAction,
    ComputerDisplay,
    ComputerObservation,
    ComputerSessionState,
)


def __getattr__(name):
    if name == "WindowsComputerRuntime":
        module = importlib.import_module(".windows_runtime", __name__)
        return module.WindowsComputerRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ComputerAction",
    "ComputerDisplay",
    "ComputerObservation",
    "ComputerSessionState",
    "ComputerSessionStore",
    "DEFAULT_DISPLAY_WIDTH",
    "DEFAULT_DISPLAY_HEIGHT",
    "PlaywrightComputerRuntime",
    "WindowsComputerRuntime",
]
