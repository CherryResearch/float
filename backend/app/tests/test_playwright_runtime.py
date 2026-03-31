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
