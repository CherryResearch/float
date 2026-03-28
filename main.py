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
        if (
            args.sticky_ports
            and isinstance(sticky_backend, int)
            and sticky_backend > 0
        ):
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

    processes = []

    # Start backend
    if not args.skip_backend:
        print(f"[INFO] Starting backend on port {args.backend_port}...")
        backend_cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(args.backend_port),
            "--reload",
        ]
        backend_proc = subprocess.Popen(
            backend_cmd, cwd=os.path.join(basedir, "backend")
        )
        processes.append(("backend", backend_proc))

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
                processes.append(("frontend", frontend_proc))

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
    def monitor(name, proc):
        code = proc.wait()
        print(f"[INFO] {name} exited with code {code}")
        # Terminate other services
        for other_name, other_proc in processes:
            if other_proc != proc and other_proc.poll() is None:
                print(
                    "[INFO] Terminating {} (PID {})".format(
                        other_name,
                        other_proc.pid,
                    )
                )
                other_proc.terminate()
        os._exit(code)

    # Launch monitors
    for name, proc in processes:
        t = threading.Thread(target=monitor, args=(name, proc), daemon=True)
        t.start()

    def shutdown(signum, frame):
        print("\n[INFO] Received signal, shutting down services...")
        for name, proc in processes:
            if proc.poll() is None:
                print(f"[INFO] Terminating {name} (PID {proc.pid})")
                proc.terminate()
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
