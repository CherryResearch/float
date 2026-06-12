import asyncio
import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)


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
                    "error" if result.get("status") == "error" else "active"
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


async def background_autonomy_runner(app: FastAPI) -> None:
    """Optional long-running loop for bounded autonomous background reflection."""

    service = _get_service(app)
    if not service.routine_enabled():
        logger.info("Background autonomy runner disabled")
        return

    mode = service.mode()
    service.start_session(mode)
    interval = service.current_interval_seconds()
    logger.info(
        "Background autonomy runner active (mode=%s poll=%.1fs)",
        mode,
        interval,
    )
    try:
        while True:
            if service.should_stop_session(mode):
                logger.info(
                    "Background autonomy runner stopped (%s)",
                    service.status(app).get("session", {}).get("stop_reason"),
                )
                return
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
                return
            interval = service.current_interval_seconds()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        logger.info("Background autonomy runner cancelled")
        raise
