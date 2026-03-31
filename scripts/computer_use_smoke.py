from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.computer_service import ComputerService  # noqa: E402


def _write_browser_pages(root: Path) -> tuple[Path, Path]:
    page_one = root / "browser_smoke_one.html"
    page_two = root / "browser_smoke_two.html"
    page_one.write_text(
        """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>browser-smoke-ready</title>
    <style>
      body { font-family: sans-serif; margin: 0; background: #f3f4f6; }
      #name { position: absolute; left: 40px; top: 40px; width: 260px; height: 44px; font-size: 24px; }
      #submit { position: absolute; left: 40px; top: 108px; width: 180px; height: 46px; font-size: 20px; }
      #status { position: absolute; left: 40px; top: 180px; font-size: 20px; color: #065f46; }
      #spacer { position: absolute; top: 260px; height: 2200px; width: 100%; }
    </style>
  </head>
  <body>
    <input id="name" />
    <button id="submit" onclick="window.document.title='submitted:'+document.getElementById('name').value; document.getElementById('status').textContent='saved:'+document.getElementById('name').value;">Submit</button>
    <div id="status">ready</div>
    <div id="spacer"></div>
  </body>
</html>
""",
        encoding="utf-8",
    )
    page_two.write_text(
        """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>browser-smoke-finished</title>
  </head>
  <body>
    <main style="font-family: sans-serif; padding: 32px;">
      <h1>Computer use smoke finished</h1>
      <p id="done">ok</p>
    </main>
  </body>
</html>
""",
        encoding="utf-8",
    )
    return page_one, page_two


def run_browser_smoke() -> None:
    service = ComputerService()
    if not service.runtimes["browser"].available():
        raise RuntimeError("Browser runtime is unavailable")

    with tempfile.TemporaryDirectory(prefix="float-browser-smoke-") as tmp_dir:
        page_one, page_two = _write_browser_pages(Path(tmp_dir))
        session = service.start_session(
            runtime="browser",
            session_id="browser-smoke",
            width=900,
            height=700,
            start_url=page_one.as_uri(),
            metadata={"smoke_test": True},
        )
        try:
            first = service.observe(session["id"])
            acted = service.act(
                session["id"],
                [
                    {"type": "click", "x": 80, "y": 70},
                    {"type": "type", "text": "hello browser"},
                    {"type": "keypress", "keys": "Tab"},
                    {"type": "keypress", "keys": "Enter"},
                    {"type": "scroll", "delta_x": 0, "delta_y": 320},
                    {"type": "wait", "ms": 200},
                ],
            )
            navigated = service.navigate(session["id"], page_two.as_uri())
        finally:
            service.stop_session(session["id"])

    first_url = first["session"].get("current_url")
    acted_title = acted["session"].get("active_window")
    final_url = navigated["session"].get("current_url")
    if first_url != page_one.as_uri():
        raise RuntimeError(f"Browser observe URL mismatch: {first_url!r}")
    if acted_title != "submitted:hello browser":
        raise RuntimeError(f"Browser action title mismatch: {acted_title!r}")
    if final_url != page_two.as_uri():
        raise RuntimeError(f"Browser navigate URL mismatch: {final_url!r}")
    print("browser_smoke=ok")
    print(f"browser_observe={first['attachment']['url']}")
    print(f"browser_action={acted['attachment']['url']}")
    print(f"browser_navigate={navigated['attachment']['url']}")


def run_windows_smoke() -> None:
    service = ComputerService()
    if not service.runtimes["windows"].available():
        raise RuntimeError("Windows runtime is unavailable")

    session = service.start_session(
        runtime="windows",
        session_id="windows-smoke",
        width=1280,
        height=720,
        metadata={"smoke_test": True},
    )
    pid: int | None = None
    try:
        baseline = service.list_windows(session["id"])
        baseline_titles = {
            item["title"]
            for item in baseline.get("windows", [])
            if isinstance(item, dict) and item.get("title")
        }
        launched = service.launch_app(session["id"], app="notepad.exe")
        pid = int(launched["pid"])
        time.sleep(1.2)
        window_listing = service.list_windows(session["id"])
        titles = [
            item["title"]
            for item in window_listing.get("windows", [])
            if isinstance(item, dict) and item.get("title")
        ]
        focus_title = next(
            (
                title
                for title in titles
                if title not in baseline_titles and "notepad" in title.lower()
            ),
            None,
        )
        if focus_title is None:
            focus_title = next(
                (title for title in titles if "notepad" in title.lower()),
                None,
            )
        if focus_title is None:
            raise RuntimeError(f"Could not find a Notepad window in {titles!r}")
        service.focus_window(session["id"], focus_title)
        time.sleep(0.4)
        acted = service.act(
            session["id"],
            [
                {"type": "type", "text": "Float computer use smoke test"},
                {"type": "keypress", "keys": "{ENTER}"},
                {"type": "type", "text": "Windows runtime ok"},
                {"type": "wait", "ms": 200},
            ],
        )
        if not acted["session"].get("active_window"):
            raise RuntimeError("Windows observe did not report an active window")
        print("windows_smoke=ok")
        print(f"windows_action={acted['attachment']['url']}")
    finally:
        if pid is not None:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        service.stop_session(session["id"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        choices=["browser", "windows", "all"],
        default="all",
    )
    args = parser.parse_args()

    if args.target in {"browser", "all"}:
        run_browser_smoke()
    if args.target in {"windows", "all"}:
        run_windows_smoke()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
