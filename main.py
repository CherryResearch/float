#!/usr/bin/env python3
"""Launcher for float project: starts backend and frontend services."""

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser


def _build_backend_cmd(port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--reload",
    ]


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Launch backend (FastAPI) and frontend (Vite) development servers."
        )
    )
    parser.add_argument(
        "--backend-port",
        type=int,
        default=0,
        help="Port for the backend server (default: auto-select)",
    )
    parser.add_argument(
        "--frontend-port",
        type=int,
        default=0,
        help="Port for the frontend dev server (default: auto-select)",
    )
    parser.add_argument(
        "--sticky-ports",
        dest="sticky_ports",
        action="store_true",
        default=True,
        help="Reuse last-used ports across restarts (default: on)",
    )
    parser.add_argument(
        "--no-sticky-ports",
        dest="sticky_ports",
        action="store_false",
        help="Disable sticky ports; auto-select new ports each run",
    )
    parser.add_argument(
        "--skip-backend",
        action="store_true",
        help="Do not start the backend server",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Do not start the frontend server",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open a browser tab",
    )
    parser.add_argument(
        "--open-once",
        action="store_true",
        help="Open browser only the first time (sticky across restarts)",
    )
    parser.add_argument(
        "--dev",
        "-dev",
        dest="dev_mode",
        action="store_true",
        help="Enable dev mode for this run (sets FLOAT_DEV_MODE=true)",
    )
    parser.add_argument(
        "--backend-auto-restart",
        dest="backend_auto_restart",
        action="store_true",
        default=True,
        help=(
            "If the backend process exits, restart it and keep the frontend alive "
            "(default: on)"
        ),
    )
    parser.add_argument(
        "--no-backend-auto-restart",
        dest="backend_auto_restart",
        action="store_false",
        help="Do not restart the backend automatically if it exits",
    )
    parser.add_argument(
        "--backend-restart-delay",
        type=float,
        default=1.0,
        help="Seconds to wait before restarting the backend after it exits",
    )
    launch_group = parser.add_mutually_exclusive_group()
    launch_group.add_argument(
        "--server",
        "--backend-only",
        dest="backend_only",
        action="store_true",
        help="Start backend only (skip frontend)",
    )
    launch_group.add_argument(
        "--ui",
        "--frontend-only",
        dest="frontend_only",
        action="store_true",
        help="Start frontend only (skip backend)",
    )
    args = parser.parse_args()

    if args.dev_mode:
        os.environ["FLOAT_DEV_MODE"] = "true"

    if args.backend_only:
        args.skip_frontend = True
    if args.frontend_only:
        args.skip_backend = True

    if args.skip_backend and args.skip_frontend:
        print("[INFO] Nothing to start. Exiting.")
        sys.exit(0)

    basedir = os.path.dirname(os.path.abspath(__file__))
    state_path = os.path.join(basedir, ".dev_state.json")

    # Load sticky state
    state = {}
    if args.sticky_ports and os.path.exists(state_path):
        try:
            import json as _json

            with open(state_path, "r", encoding="utf-8") as f:
                state = _json.load(f) or {}
        except Exception:
            state = {}

    # Auto-select or reuse ports if set to 0
    def _choose_port():
        s = socket.socket()
        s.bind(("0.0.0.0", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    if not args.skip_backend and args.backend_port == 0:
        sticky_backend = state.get("backend_port")
        if args.sticky_ports and isinstance(sticky_backend, int) and sticky_backend > 0:
            args.backend_port = sticky_backend
        else:
            args.backend_port = _choose_port()
        print(f"[INFO] Using backend port {args.backend_port}")
    if not args.skip_frontend and args.frontend_port == 0:
        sticky_frontend = state.get("frontend_port")
        if (
            args.sticky_ports
            and isinstance(sticky_frontend, int)
            and sticky_frontend > 0
        ):
            args.frontend_port = sticky_frontend
        else:
            args.frontend_port = _choose_port()
        print(f"[INFO] Using frontend port {args.frontend_port}")

    processes: dict[str, subprocess.Popen] = {}
    processes_lock = threading.Lock()
    shutting_down = threading.Event()

    def _register_process(name: str, proc: subprocess.Popen) -> None:
        with processes_lock:
            processes[name] = proc

    def _start_monitor(name: str, proc: subprocess.Popen) -> None:
        threading.Thread(target=monitor, args=(name, proc), daemon=True).start()

    def _active_process_items() -> list[tuple[str, subprocess.Popen]]:
        with processes_lock:
            return list(processes.items())

    def _launch_backend() -> subprocess.Popen:
        print(f"[INFO] Starting backend on port {args.backend_port}...")
        backend_proc = subprocess.Popen(
            _build_backend_cmd(args.backend_port),
            cwd=os.path.join(basedir, "backend"),
        )
        _register_process("backend", backend_proc)
        _start_monitor("backend", backend_proc)
        return backend_proc

    def _terminate_service(name: str, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        print(f"[INFO] Terminating {name} (PID {proc.pid})")
        proc.terminate()

    def _terminate_other_services(exclude: str | None = None) -> None:
        for other_name, other_proc in _active_process_items():
            if exclude and other_name == exclude:
                continue
            _terminate_service(other_name, other_proc)

    def monitor(name: str, proc: subprocess.Popen) -> None:
        code = proc.wait()
        print(f"[INFO] {name} exited with code {code}")
        if shutting_down.is_set():
            return
        with processes_lock:
            current = processes.get(name)
            if current is not proc:
                return
        if name == "backend" and args.backend_auto_restart:
            delay = max(0.0, float(args.backend_restart_delay or 0.0))
            print(
                "[INFO] Backend exited; keeping the frontend up and restarting "
                f"the backend in {delay:.1f}s..."
            )
            if delay:
                time.sleep(delay)
            if shutting_down.is_set():
                return
            try:
                _launch_backend()
                return
            except Exception as exc:
                print(f"[ERROR] Failed to restart backend: {exc}")
        _terminate_other_services(exclude=name)
        os._exit(code)

    # Start backend
    if not args.skip_backend:
        _launch_backend()

    # Start frontend
    if not args.skip_frontend:
        print(f"[INFO] Starting frontend on port {args.frontend_port}...")
        frontend_env = os.environ.copy()
        # Pass ports to the Vite dev server
        frontend_env["VITE_PORT"] = str(args.frontend_port)
        frontend_env["BACKEND_PORT"] = str(args.backend_port)
        # Use npm.cmd on Windows for compatibility
        npm_exe = shutil.which("npm") or shutil.which("npm.cmd")
        if not npm_exe:
            print(
                "[ERROR] npm not found on PATH. Install Node.js and npm "
                "to use the frontend."
            )
            args.skip_frontend = True
        else:
            frontend_dir = os.path.join(basedir, "frontend")
            vite_path = os.path.join(
                frontend_dir,
                "node_modules",
                ".bin",
                "vite",
            )
            if not os.path.exists(vite_path):
                print("[INFO] Installing frontend dependencies...")
                install_cmd = [npm_exe, "install"]
                subprocess.run(install_cmd, cwd=frontend_dir, check=False)

            frontend_cmd = [npm_exe, "run", "dev"]
            try:
                frontend_proc = subprocess.Popen(
                    frontend_cmd,
                    cwd=frontend_dir,
                    env=frontend_env,
                )
            except FileNotFoundError:
                print(
                    "[ERROR] Failed to launch frontend process. Ensure npm "
                    "is installed correctly."
                )
                args.skip_frontend = True
            else:
                _register_process("frontend", frontend_proc)
                _start_monitor("frontend", frontend_proc)

                def _open_browser():
                    time.sleep(2)
                    url = f"http://localhost:{args.frontend_port}"
                    print(f"[INFO] Opening {url} in your browser...")
                    webbrowser.open(url)

                should_open = not args.no_open
                if args.open_once:
                    should_open = should_open and not bool(state.get("browser_opened"))
                if should_open and args.frontend_port > 0:
                    threading.Thread(target=_open_browser, daemon=True).start()
                    # Update sticky state to mark browser opened
                    state["browser_opened"] = True

    # Persist sticky state
    try:
        if args.sticky_ports and (not args.skip_backend or not args.skip_frontend):
            import json as _json

            if not args.skip_backend:
                state["backend_port"] = args.backend_port
            if not args.skip_frontend:
                state["frontend_port"] = args.frontend_port
            with open(state_path, "w", encoding="utf-8") as f:
                _json.dump(state, f, indent=2)
    except Exception:
        pass

    def shutdown(signum, frame):
        print("\n[INFO] Received signal, shutting down services...")
        shutting_down.set()
        for name, proc in _active_process_items():
            _terminate_service(name, proc)
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)

    # Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
