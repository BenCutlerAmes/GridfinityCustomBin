"""Start Tooltracer: by default as a desktop window, optionally in the browser."""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from . import __version__, server

HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _ensure_streams() -> None:
    """A packaged windowed app has no console: stdout/stderr are None, which breaks logging.
    Send them to a log file in the settings folder instead (also handy for bug reports)."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        server.STATE.cfg_dir.mkdir(parents=True, exist_ok=True)
        log = open(server.STATE.cfg_dir / "tooltracer.log", "w", encoding="utf-8", buffering=1)
    except OSError:
        import os

        log = open(os.devnull, "w")
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def _serve_in_thread(port: int) -> uvicorn.Server:
    srv = uvicorn.Server(uvicorn.Config(server.app, host=HOST, port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.time() + 15
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    if not srv.started:
        raise RuntimeError("The local server did not start.")
    return srv


def run_window(on_ready=None) -> bool:
    """Open the app in its own window. Returns False if no window backend is available."""
    try:
        import webview
    except Exception as e:  # missing GUI backend
        print(f"Desktop window unavailable ({e}); falling back to the browser.")
        return False
    port = _free_port()
    srv = _serve_in_thread(port)
    window = webview.create_window("Tooltracer", f"http://{HOST}:{port}", width=1320, height=880, min_size=(980, 640))
    server.STATE.window = window
    try:
        # private_mode=False keeps small UI preferences (last mode) between runs.
        webview.start(on_ready, window, private_mode=False, storage_path=str(server.STATE.cfg_dir / "webview"))
    finally:
        server.STATE.window = None
        srv.should_exit = True
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tooltracer", description="Photo-to-Gridfinity tool tracer")
    ap.add_argument("--version", action="version", version=f"Tooltracer {__version__}")
    ap.add_argument("--browser", action="store_true", help="open in the default browser instead of a desktop window")
    ap.add_argument("--no-browser", action="store_true", help="only run the local server (implies browser mode)")
    ap.add_argument("--port", type=int, default=8765, help="port for browser mode (default 8765)")
    ap.add_argument("--library", help="use this library folder for this run without changing the saved setting")
    ap.add_argument("--config-dir", help="where to keep the settings file (default: per-user config folder)")
    args = ap.parse_args(argv)

    server.init_state(args.config_dir, args.library)
    _ensure_streams()
    print(f"Tooltracer {__version__}")

    if not (args.browser or args.no_browser) and run_window():
        return
    url = f"http://{HOST}:{args.port}"
    print(f"Tooltracer running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(server.app, host=HOST, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
