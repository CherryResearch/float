import asyncio
import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)

_DISABLED_RECHECK_SECONDS = 30.0


def _get_service(app: FastAPI):
    service = getattr(app.state, "background_autonomy_service", None)
    if service is not None:
        return service
    from app.services.background_autonomy_service import BackgroundAutonomyService

    config = getattr(app.state, "config", None)
    reflection_service = getattr(app.state, "reflection_service", None)
    service = BackgroundAutonomyService(
        config if isinstance(config, dict) else {},
        reflection_service=reflection_service,
    )
    app.state.background_autonomy_service = service
    return service


async def _publish_tick(app: FastAPI, result: dict) -> None:
    try:
        from app import routes

        await routes.publish_console_event(
            app,
            {
                "type": "task",
                "id": result.get("id"),
                "status": result.get("status"),
                "agent_status": (
                    "error"
                    if result.get("status") == "error"
                    else (
                        "active"
                        if result.get("status") in {"busy", "running"}
                        else "idle"
                    )
                ),
                "agent_id": "system:background-autonomy",
                "agent_label": "background autonomy",
                "content": (
                    f"Autonomy tick {result.get('status')}: "
                    f"{result.get('ran_reflections') or 0} reflection run(s), "
                    f"{result.get('candidate_count') or 0} candidate(s)."
                ),
                "metadata": {"background_autonomy": result},
            },
            default_agent="system:background-autonomy",
        )
    except Exception:
        logger.debug("Failed to publish background autonomy tick", exc_info=True)


def _get_wakeup_event(app: FastAPI) -> asyncio.Event:
    wakeup = getattr(app.state, "background_autonomy_wakeup", None)
    if isinstance(wakeup, asyncio.Event):
        return wakeup
    wakeup = asyncio.Event()
    app.state.background_autonomy_wakeup = wakeup
    return wakeup


async def _wait_for_wakeup(wakeup: asyncio.Event, timeout: float) -> bool:
    """Wait until configuration changes or the next supervisor poll is due."""

    if wakeup.is_set():
        wakeup.clear()
        return True
    try:
        await asyncio.wait_for(wakeup.wait(), timeout=max(0.0, float(timeout)))
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        wakeup.clear()


async def background_autonomy_runner(app: FastAPI) -> None:
    """Supervise bounded background reflection across live config changes."""

    service = _get_service(app)
    wakeup = _get_wakeup_event(app)
    active_mode = None
    try:
        while True:
            if not service.routine_enabled():
                if active_mode is not None:
                    logger.info("Background autonomy runner disabled")
                active_mode = None
                await _wait_for_wakeup(wakeup, _DISABLED_RECHECK_SECONDS)
                continue

            mode = service.mode()
            if active_mode != mode:
                service.start_session(mode)
                active_mode = mode
                logger.info(
                    "Background autonomy runner active (mode=%s poll=%.1fs)",
                    mode,
                    service.current_interval_seconds(),
                )

            if service.should_stop_session(mode):
                logger.info(
                    "Background autonomy runner idle at stop condition (%s)",
                    service.status(app).get("session", {}).get("stop_reason"),
                )
                await _wait_for_wakeup(wakeup, _DISABLED_RECHECK_SECONDS)
                continue
            try:
                result = await asyncio.to_thread(service.tick, app, mode=mode)
                await _publish_tick(app, result)
            except asyncio.CancelledError:  # pragma: no cover - shutdown path
                raise
            except Exception:
                logger.exception("Background autonomy runner iteration failed")
            if service.should_stop_session(mode):
                logger.info(
                    "Background autonomy runner reached stop condition (%s)",
                    service.status(app).get("session", {}).get("stop_reason"),
                )
            interval = service.current_interval_seconds()
            await _wait_for_wakeup(wakeup, interval)
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        logger.info("Background autonomy runner cancelled")
        raise
