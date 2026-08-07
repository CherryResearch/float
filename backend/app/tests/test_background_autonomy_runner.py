from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from types import SimpleNamespace


def test_background_runner_stays_alive_and_reloads_runtime_config(monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from workers import background_autonomy_runner as runner_module

    class FakeService:
        def __init__(self):
            self.enabled = False
            self.current_mode = "basic"
            self.started_modes = []
            self.tick_modes = []

        def routine_enabled(self):
            return self.enabled

        def mode(self):
            return self.current_mode

        def start_session(self, mode):
            self.started_modes.append(mode)

        def should_stop_session(self, _mode):
            return False

        def current_interval_seconds(self):
            return 3600.0

        def tick(self, _app, *, mode):
            self.tick_modes.append(mode)
            return {"id": f"tick-{len(self.tick_modes)}", "status": "idle"}

        def status(self, _app):
            return {"session": {"stop_reason": None}}

    async def wait_for_count(items, count):
        async def ready():
            while len(items) < count:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(ready(), timeout=2.0)

    async def scenario():
        service = FakeService()
        app = SimpleNamespace(
            state=SimpleNamespace(background_autonomy_service=service)
        )
        published = []

        async def capture_tick(_app, result):
            published.append(result)

        monkeypatch.setattr(runner_module, "_publish_tick", capture_tick)
        task = asyncio.create_task(runner_module.background_autonomy_runner(app))
        try:
            await asyncio.sleep(0)
            assert not task.done()
            assert service.tick_modes == []

            service.enabled = True
            app.state.background_autonomy_wakeup.set()
            await wait_for_count(service.tick_modes, 1)
            assert service.tick_modes == ["basic"]

            service.current_mode = "overnight"
            app.state.background_autonomy_wakeup.set()
            await wait_for_count(service.tick_modes, 2)
            assert service.tick_modes == ["basic", "overnight"]
            assert service.started_modes == ["basic", "overnight"]
            assert [item["status"] for item in published] == ["idle", "idle"]
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
