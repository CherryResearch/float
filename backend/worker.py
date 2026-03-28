"""Celery worker entrypoint."""

import logging

from app.agents.engine import MultiAgentEngine  # noqa: F401  Ensure agents are loaded
from app.tasks import celery_app
from app import config as app_config
from app.utils.telemetry import configure_logging, configure_tracing


def _init_worker_telemetry() -> None:
    cfg = app_config.load_config()
    configure_logging(cfg)
    configure_tracing(cfg)
    logging.getLogger(__name__).info("Celery worker telemetry initialized")


if __name__ == "__main__":
    _init_worker_telemetry()
    celery_app.worker_main(["worker"])
    # Celery handles async tasks to handle float's specifications
