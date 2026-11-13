"""Start Gridfinity Custom Bin: by default as a desktop window, optionally in the browser."""
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
        log = open(server.STATE.cfg_dir / "gridfinity-custom-bin.log", "w", encoding="utf-8", buffering=1)
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
    window = webview.create_window("Gridfinity Custom Bin", f"http://{HOST}:{port}", width=1320, height=880, min_size=(980, 640))
    server.STATE.window = window
    try:
        # private_mode=False keeps small UI preferences (last mode) between runs.
        webview.start(on_ready, window, private_mode=False, storage_path=str(server.STATE.cfg_dir / "webview"))
    finally:
        server.STATE.window = None
        srv.should_exit = True
    return True


_WINDOW_CHECK_JS = """(function () {
  var v = document.getElementById('appVersion');
  var c = document.createElement('canvas');
  return {
    ready: document.readyState === 'complete' && !!v && v.textContent !== '',
    title: document.title,
    modes: document.querySelectorAll('#modes button').length,
    version: v ? v.textContent : null,
    layout: !!document.getElementById('layoutSvg'),
    webgl: !!(c.getContext('webgl2') || c.getContext('webgl'))
  };
})()"""


def run_window_test(out_path: str, hold: float = 6.0) -> bool:
    """Open the real desktop window, check the UI loaded inside it, write the result as JSON,
    keep the window up briefly (so a screenshot can be taken) and close. Used by the build tests."""
    import json
    from pathlib import Path

    result = {"ok": False, "error": "window never became ready"}

    def check(window):
        nonlocal result
        try:
            info, deadline = {}, time.time() + 45
            while time.time() < deadline:
                try:
                    info = window.evaluate_js(_WINDOW_CHECK_JS) or {}
                except Exception as e:  # page still loading
                    info = {"error": repr(e)}
                if info.get("ready"):
                    break
                time.sleep(0.5)
            ok = bool(info.get("ready")) and info.get("modes") == 4 and info.get("layout") is True
            result = {"ok": ok, "native": server.STATE.window is not None, **info}
        except Exception as e:
            result = {"ok": False, "error": repr(e)}
        Path(out_path).write_text(json.dumps(result), encoding="utf-8")
        time.sleep(hold)
        window.destroy()

    if not run_window(check):
        result = {"ok": False, "error": "no window backend available"}
    if not Path(out_path).exists():
        Path(out_path).write_text(json.dumps(result), encoding="utf-8")
    return bool(result.get("ok"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gridfinity-custom-bin", description="Gridfinity Custom Bin: photo to Gridfinity bin")
    ap.add_argument("--version", action="version", version=f"Gridfinity Custom Bin {__version__}")
    ap.add_argument("--browser", action="store_true", help="open in the default browser instead of a desktop window")
    ap.add_argument("--no-browser", action="store_true", help="only run the local server (implies browser mode)")
    ap.add_argument("--port", type=int, default=8765, help="port for browser mode (default 8765)")
    ap.add_argument("--library", help="use this library folder for this run without changing the saved setting")
    ap.add_argument("--config-dir", help="where to keep the settings file (default: per-user config folder)")
    ap.add_argument("--window-test", metavar="RESULT.json", help=argparse.SUPPRESS)  # used by the build tests
    args = ap.parse_args(argv)

    server.init_state(args.config_dir, args.library)
    _ensure_streams()
    print(f"Gridfinity Custom Bin {__version__}")

    if args.window_test:
        sys.exit(0 if run_window_test(args.window_test) else 1)

    if not (args.browser or args.no_browser) and run_window():
        return
    url = f"http://{HOST}:{args.port}"
    print(f"Gridfinity Custom Bin running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(server.app, host=HOST, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
