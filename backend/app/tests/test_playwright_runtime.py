import pytest


def test_playwright_runtime_reports_missing_browser_binaries(monkeypatch, tmp_path):
    from app.computer import playwright_runtime as runtime_mod

    class DummyChromium:
        def launch_persistent_context(self, *_args, **_kwargs):
            raise RuntimeError(
                "BrowserType.launch_persistent_context: Executable doesn't exist at "
                "C:\\Users\\kaist\\AppData\\Local\\ms-playwright\\chromium\\chrome.exe\n"
                "Please run the following command to download new browsers: playwright install"
            )

    class DummyPlaywright:
        chromium = DummyChromium()

    class DummyStarter:
        def start(self):
            return DummyPlaywright()

    monkeypatch.setattr(runtime_mod, "sync_playwright", lambda: DummyStarter())

    runtime = runtime_mod.PlaywrightComputerRuntime(screenshot_root=tmp_path)
    with pytest.raises(RuntimeError) as excinfo:
        runtime.start_session(session_id="browser-test", width=800, height=600)

    assert str(excinfo.value) == (
        "Playwright browser binaries are not installed. "
        "Run 'playwright install chromium' and try again."
    )


def test_playwright_runtime_shutdown_closes_all_handles(tmp_path):
    from app.computer import playwright_runtime as runtime_mod

    class DummyBrowser:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class DummyPlaywright:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    browser = DummyBrowser()
    playwright = DummyPlaywright()

    runtime = runtime_mod.PlaywrightComputerRuntime(screenshot_root=tmp_path)
    runtime._playwright = playwright
    runtime._sessions["browser-test"] = {
        "browser": browser,
        "page": object(),
        "profile_dir": profile_dir,
    }

    result = runtime.shutdown()

    assert result == {
        "status": "stopped",
        "runtime": "browser",
        "closed_sessions": ["browser-test"],
    }
    assert browser.closed is True
    assert playwright.stopped is True
    assert runtime._sessions == {}
    assert profile_dir.exists() is False
