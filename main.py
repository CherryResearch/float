#!/usr/bin/env python3
"""Launcher for float project: starts backend and frontend services."""

import argparse
import importlib.util
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import urlparse


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


def _build_worker_cmd() -> list[str]:
    return [sys.executable, "worker.py"]


def _can_connect(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_port(
    host: str,
    port: int,
    timeout_seconds: float = 12.0,
    *,
    interval_seconds: float = 0.3,
) -> bool:
    deadline = time.time() + max(0.0, timeout_seconds)
    while time.time() < deadline:
        if _can_connect(host, port):
            return True
        time.sleep(max(0.05, interval_seconds))
    return _can_connect(host, port)


def _broker_endpoint(url: str) -> tuple[str, int] | None:
    text = str(url or "").strip()
    if not text:
        return None
    try:
        parsed = urlparse(text)
    except Exception:
        return None
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname
    default_port = {
        "redis": 6379,
        "rediss": 6379,
        "amqp": 5672,
        "amqps": 5671,
    }.get(scheme)
    port = parsed.port or default_port
    if not host or not port:
        return None
    return host, int(port)


def _broker_reachable(url: str) -> bool | None:
    endpoint = _broker_endpoint(url)
    if endpoint is None:
        return None
    host, port = endpoint
    return _can_connect(host, port, timeout=0.5)


def _resolve_compose_command() -> list[str] | None:
    docker_exe = shutil.which("docker")
    if docker_exe:
        try:
            probe = subprocess.run(
                [docker_exe, "compose", "version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if probe.returncode == 0:
                return [docker_exe, "compose"]
        except Exception:
            pass
    docker_compose_exe = shutil.which("docker-compose")
    if docker_compose_exe:
        return [docker_compose_exe]
    return None


def _run_compose_service(
    basedir: str,
    service: str,
    *,
    action: str,
) -> bool:
    compose_cmd = _resolve_compose_command()
    if not compose_cmd:
        return False
    if action == "up":
        cmd = [*compose_cmd, "up", "-d", service]
    elif action == "stop":
        cmd = [*compose_cmd, "stop", service]
    else:
        raise ValueError(f"Unsupported compose action: {action}")
    try:
        proc = subprocess.run(cmd, cwd=basedir, check=False)
        return proc.returncode == 0
    except Exception:
        return False


def _python_module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


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
    parser.add_argument(
        "--with-worker",
        dest="with_worker",
        action="store_true",
        default=True,
        help="Start a local Celery worker process for background-task testing (default: on).",
    )
    parser.add_argument(
        "--no-worker",
        dest="with_worker",
        action="store_false",
        help="Do not start the local Celery worker.",
    )
    parser.add_argument(
        "--with-redis",
        dest="with_redis",
        action="store_true",
        default=True,
        help="Start the local Redis broker via Docker Compose on host port 6380 (default: on).",
    )
    parser.add_argument(
        "--no-redis",
        dest="with_redis",
        action="store_false",
        help="Do not start the local Redis broker.",
    )
    parser.add_argument(
        "--agents",
        action="store_true",
        help="Start both the local Redis broker and Celery worker.",
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

    if args.agents:
        args.with_worker = True
        args.with_redis = True

    if args.backend_only:
        args.skip_frontend = True
    if args.frontend_only:
        args.skip_backend = True
    if args.skip_backend:
        args.with_worker = False
        args.with_redis = False

    if args.skip_backend and args.skip_frontend:
        print("[INFO] Nothing to start. Exiting.")
        sys.exit(0)

    basedir = os.path.dirname(os.path.abspath(__file__))
    state_path = os.path.join(basedir, ".dev_state.json")
    service_env = os.environ.copy()
    original_broker_url = service_env.get("CELERY_BROKER_URL")
    original_result_backend = service_env.get("CELERY_RESULT_BACKEND")
    original_redis_url = service_env.get("REDIS_URL")
    managed_redis = False
    redis_ready = False
    worker_started = False
    local_celery_broker = "redis://127.0.0.1:6380/0"
    requested_broker = (
        local_celery_broker
        if args.with_redis
        else service_env.get("CELERY_BROKER_URL") or "redis://localhost:6379/0"
    )
    if requested_broker.lower().startswith(
        ("redis://", "rediss://")
    ) and not _python_module_available("redis"):
        if args.with_worker or args.with_redis:
            print(
                "[WARN] Python package 'redis' is not installed in this environment. "
                "Run 'poetry install' to repair the base environment before using "
                "--with-worker, --with-redis, or --agents."
            )
        args.with_worker = False
        args.with_redis = False
    if args.with_redis:
        service_env["CELERY_BROKER_URL"] = local_celery_broker
        service_env["CELERY_RESULT_BACKEND"] = local_celery_broker
        service_env.setdefault("REDIS_URL", local_celery_broker)

    # Load the last known launcher state. Sticky-port reuse reads the sticky
    # fields when present; UI helpers and smoke scripts read the current ports.
    state = {}
    if os.path.exists(state_path):
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

    def _sticky_port(state_key: str) -> int | None:
        sticky_key = f"sticky_{state_key}"
        value = state.get(sticky_key, state.get(state_key))
        return value if isinstance(value, int) and value > 0 else None

    if not args.skip_backend and args.backend_port == 0:
        sticky_backend = _sticky_port("backend_port")
        if args.sticky_ports and sticky_backend is not None:
            args.backend_port = sticky_backend
        else:
            args.backend_port = _choose_port()
        print(f"[INFO] Using backend port {args.backend_port}")
    if not args.skip_frontend and args.frontend_port == 0:
        sticky_frontend = _sticky_port("frontend_port")
        if args.sticky_ports and sticky_frontend is not None:
            args.frontend_port = sticky_frontend
        else:
            args.frontend_port = _choose_port()
        print(f"[INFO] Using frontend port {args.frontend_port}")

    processes: dict[str, subprocess.Popen] = {}
    processes_lock = threading.Lock()
    shutting_down = threading.Event()

    def _write_launcher_state() -> None:
        try:
            import json as _json

            with processes_lock:
                process_state = {
                    name: {
                        "pid": proc.pid,
                        "running": proc.poll() is None,
                        "returncode": proc.poll(),
                    }
                    for name, proc in sorted(processes.items())
                }
            state["launcher_pid"] = os.getpid()
            state["launcher_running"] = not shutting_down.is_set()
            state["processes"] = process_state
            state["updated_at_epoch"] = time.time()
            with open(state_path, "w", encoding="utf-8") as f:
                _json.dump(state, f, indent=2)
        except Exception:
            pass

    def _register_process(name: str, proc: subprocess.Popen) -> None:
        with processes_lock:
            processes[name] = proc
        _write_launcher_state()

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
            env=service_env,
        )
        _register_process("backend", backend_proc)
        _start_monitor("backend", backend_proc)
        return backend_proc

    def _launch_worker() -> subprocess.Popen:
        broker_url = service_env.get("CELERY_BROKER_URL") or "redis://localhost:6379/0"
        print(f"[INFO] Starting Celery worker using broker {broker_url}...")
        worker_proc = subprocess.Popen(
            _build_worker_cmd(),
            cwd=os.path.join(basedir, "backend"),
            env=service_env,
        )
        _register_process("worker", worker_proc)
        _start_monitor("worker", worker_proc)
        return worker_proc

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

    if args.with_redis:
        if _can_connect("127.0.0.1", 6380):
            print("[INFO] Reusing Redis broker on 127.0.0.1:6380")
            redis_ready = True
        else:
            print("[INFO] Starting Redis broker via Docker Compose...")
            if _run_compose_service(basedir, "redis", action="up"):
                if _wait_for_port("127.0.0.1", 6380, timeout_seconds=15.0):
                    managed_redis = True
                    redis_ready = True
                    print("[INFO] Redis broker ready on 127.0.0.1:6380")
                else:
                    print(
                        "[WARN] Redis compose service started but 127.0.0.1:6380 "
                        "did not become reachable in time."
                    )
            else:
                print(
                    "[WARN] Could not start Redis via Docker Compose. "
                    "Install Docker or start a broker manually."
                )
        if not redis_ready:
            args.with_redis = False
            if original_broker_url is None:
                service_env.pop("CELERY_BROKER_URL", None)
            else:
                service_env["CELERY_BROKER_URL"] = original_broker_url
            if original_result_backend is None:
                service_env.pop("CELERY_RESULT_BACKEND", None)
            else:
                service_env["CELERY_RESULT_BACKEND"] = original_result_backend
            if original_redis_url is None:
                service_env.pop("REDIS_URL", None)
            else:
                service_env["REDIS_URL"] = original_redis_url

    # Start backend
    if not args.skip_backend:
        _launch_backend()

    if args.with_worker:
        broker_url = service_env.get("CELERY_BROKER_URL") or requested_broker
        broker_reachable = _broker_reachable(broker_url)
        if broker_reachable is False:
            print(
                f"[WARN] Celery broker {broker_url} is unavailable. "
                "Skipping worker startup."
            )
            args.with_worker = False
        else:
            _launch_worker()
            worker_started = True

    # Start frontend
    if not args.skip_frontend:
        print(f"[INFO] Starting frontend on port {args.frontend_port}...")
        frontend_env = service_env.copy()
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
    if not args.skip_backend or not args.skip_frontend:
        if not args.skip_backend:
            state["backend_port"] = args.backend_port
            if args.sticky_ports:
                state["sticky_backend_port"] = args.backend_port
        if not args.skip_frontend:
            state["frontend_port"] = args.frontend_port
            if args.sticky_ports:
                state["sticky_frontend_port"] = args.frontend_port
        state["worker_enabled"] = bool(worker_started)
        state["redis_enabled"] = bool(redis_ready)
        if args.with_worker or args.with_redis:
            state["celery_broker_url"] = service_env.get(
                "CELERY_BROKER_URL", local_celery_broker
            )
        else:
            state.pop("celery_broker_url", None)
        _write_launcher_state()

    def shutdown(signum, frame):
        print("\n[INFO] Received signal, shutting down services...")
        shutting_down.set()
        for name, proc in _active_process_items():
            _terminate_service(name, proc)
        state["launcher_running"] = False
        state["shutdown_requested_at_epoch"] = time.time()
        _write_launcher_state()
        if managed_redis:
            print("[INFO] Stopping Redis broker...")
            _run_compose_service(basedir, "redis", action="stop")
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
