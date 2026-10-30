"""Run importable callables in disposable child processes.

The parent owns all durable application state.  A child may perform one
non-cooperative operation and return its value, but it never writes Float's
canonical Calendar, Activity, or conversation records.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import multiprocessing
from collections.abc import Awaitable, Callable, Mapping
from multiprocessing.connection import Connection
from typing import Any, Optional


class IsolatedProcessError(RuntimeError):
    """A child callable failed before returning a result."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "RuntimeError",
        error_module: str = "builtins",
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.error_module = error_module
        self.status_code = status_code


class IsolatedProcessTerminated(RuntimeError):
    """The parent deliberately stopped a still-running child."""

    def __init__(self, reason: str) -> None:
        normalized = str(reason or "termination_requested").strip()
        super().__init__(normalized)
        self.reason = normalized


def _isolated_process_entry(
    connection: Connection,
    module_name: str,
    callable_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    try:
        module = importlib.import_module(module_name)
        target = getattr(module, callable_name)
        result = target(*args, **kwargs)
        connection.send({"status": "ok", "result": result})
    except BaseException as exc:  # Child failures must cross a pickle boundary.
        status_code = getattr(exc, "status_code", None)
        try:
            status_code = int(status_code) if status_code is not None else None
        except (TypeError, ValueError, OverflowError):
            status_code = None
        connection.send(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "error_module": type(exc).__module__,
                "status_code": status_code,
                "message": str(exc)[:2000],
            }
        )
    finally:
        connection.close()


async def _termination_reason(
    termination_check: Optional[Callable[[], Optional[str] | Awaitable[Optional[str]]]],
) -> Optional[str]:
    if termination_check is None:
        return None
    result = termination_check()
    if inspect.isawaitable(result):
        result = await result
    normalized = str(result or "").strip()
    return normalized or None


def _stop_process(process: multiprocessing.Process, *, grace_seconds: float) -> None:
    if not process.is_alive():
        process.join(timeout=grace_seconds)
        return
    process.terminate()
    process.join(timeout=grace_seconds)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=grace_seconds)


async def run_isolated_process(
    module_name: str,
    callable_name: str,
    *args: Any,
    kwargs: Optional[Mapping[str, Any]] = None,
    termination_check: Optional[
        Callable[[], Optional[str] | Awaitable[Optional[str]]]
    ] = None,
    poll_interval: float = 0.05,
    termination_grace: float = 1.0,
) -> Any:
    """Run one importable callable in a spawned process.

    ``termination_check`` returns a short reason when the child should stop.
    Cancellation of the parent coroutine also terminates the child before the
    cancellation is allowed to propagate.
    """

    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_isolated_process_entry,
        args=(
            child_connection,
            module_name,
            callable_name,
            tuple(args),
            dict(kwargs or {}),
        ),
        name=f"float-isolated-{callable_name}",
    )
    process.daemon = False
    try:
        process.start()
    except BaseException:
        parent_connection.close()
        child_connection.close()
        raise
    child_connection.close()
    try:
        while True:
            reason = await _termination_reason(termination_check)
            if reason:
                await asyncio.to_thread(
                    _stop_process, process, grace_seconds=termination_grace
                )
                raise IsolatedProcessTerminated(reason)
            if parent_connection.poll():
                try:
                    payload = parent_connection.recv()
                except EOFError as exc:
                    raise IsolatedProcessError(
                        "isolated process closed its result pipe unexpectedly",
                        error_type="ProcessPipeError",
                        error_module=__name__,
                    ) from exc
                await asyncio.to_thread(process.join, termination_grace)
                if payload.get("status") == "ok":
                    return payload.get("result")
                raise IsolatedProcessError(
                    str(payload.get("message") or "isolated process failed"),
                    error_type=str(payload.get("error_type") or "RuntimeError"),
                    error_module=str(payload.get("error_module") or "builtins"),
                    status_code=payload.get("status_code"),
                )
            if not process.is_alive():
                await asyncio.to_thread(process.join, termination_grace)
                raise IsolatedProcessError(
                    f"isolated process exited with code {process.exitcode} without a result",
                    error_type="ProcessExitError",
                    error_module=__name__,
                )
            await asyncio.sleep(max(0.01, poll_interval))
    finally:
        parent_connection.close()
        if process.is_alive():
            await asyncio.shield(
                asyncio.to_thread(
                    _stop_process, process, grace_seconds=termination_grace
                )
            )


def generate_with_fresh_service(request: Mapping[str, Any]) -> Any:
    """Generate once with process-local provider state."""

    from app.services import LLMService, ModelContext
    from app.utils import conversation_store
    from app.utils.privacy_policy import DataPolicy, policy_for_destination

    context_data = request.get("context")
    context_data = context_data if isinstance(context_data, Mapping) else {}
    context = ModelContext(
        system_prompt=str(context_data.get("system_prompt") or ""),
        messages=list(context_data.get("messages") or []),
        tools=list(context_data.get("tools") or []),
        metadata=dict(context_data.get("metadata") or {}),
    )
    config = request.get("config")
    service = LLMService(
        mode=str(request.get("mode") or "api"),
        config=dict(config) if isinstance(config, Mapping) else None,
    )
    generate_kwargs = {
        "session_id": str(request.get("session_id") or "default"),
        "response_format": request.get("response_format"),
        "context": context,
    }
    # ContextVars do not cross spawn. Validate and reconstruct the restriction,
    # independently retaining a graph marker even if a caller omitted the policy.
    policy_data = request.get("data_policy")
    policy = None
    if policy_data is not None:
        if (
            not isinstance(policy_data, Mapping)
            or not {"external", "allow_remote_query"}.issubset(policy_data)
            or set(policy_data) - {"external", "allow_remote_query", "local_only"}
            or any(type(value) is not bool for value in policy_data.values())
        ):
            raise ValueError("Invalid serialized provider data policy")
        policy = DataPolicy(**policy_data)
    if conversation_store.context_graph_local_only(
        None, context.messages, context.metadata
    ):
        policy = DataPolicy(False, False)
    if policy is not None:
        if policy.local_only or not policy.external:
            mode = getattr(service, "mode", None)
            endpoint = (
                (getattr(service, "config", None) or {}).get("server_url")
                if mode == "server"
                else None
            )
            if policy_for_destination(mode, endpoint).external:
                raise PermissionError(
                    "Scheduled generation contains local-only context; use a local model."
                )
        generate_kwargs["_data_policy"] = policy
    model = request.get("model")
    if isinstance(model, str) and model.strip():
        generate_kwargs["model"] = model.strip()
    return service.generate([], **generate_kwargs)
