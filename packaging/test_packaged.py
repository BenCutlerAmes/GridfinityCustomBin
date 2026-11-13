"""Test a built Gridfinity Custom Bin app (the PyInstaller output or an installed copy) from the outside.

    uv run python packaging/test_packaged.py --exe dist/GridfinityCustomBin/GridfinityCustomBin.exe --version 0.3.0 --ai --window

Headless part: starts the app's server and drives the real flow over HTTP - upload a synthetic
photo with known tool sizes, trace it, save tools to a library, save a bin, export an STL.
--ai      also traces with the AI model (downloads it once: proves ONNX Runtime and HTTPS work when packaged)
--window  also opens the real desktop window, checks the UI loaded in it and saves a screenshot

Exits non-zero on the first failure. Results and logs go to --out (default: test-output/).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import cv2
import httpx

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = sorted([(24.0, 150.0), (30.0, 30.0), (40.0, 100.0)])  # tools in the synthetic photo, mm
TOL = 1.5


def log(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    log(f"FAIL: {msg}")
    sys.exit(1)


def check(cond, msg: str) -> None:
    if not cond:
        fail(msg)
    log(f"  ok  {msg}")


def test_photo() -> bytes:
    spec = importlib.util.spec_from_file_location("make_test_photo", ROOT / "scripts" / "make_test_photo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, buf = cv2.imencode(".jpg", mod.make(False), [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buf.tobytes()


def sizes(tools) -> list:
    return sorted(tuple(sorted((t["width"], t["height"]))) for t in tools)


def sizes_match(tools, expected) -> bool:
    found = sizes(tools)
    return len(found) == len(expected) and all(abs(f[0] - e[0]) <= TOL and abs(f[1] - e[1]) <= TOL for f, e in zip(found, expected))


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()  # this process only - never by name
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def show_log(cfg: Path) -> None:
    f = cfg / "gridfinity-custom-bin.log"
    if f.exists():
        log(f"--- {f} ---\n{f.read_text(errors='replace')[-4000:]}\n---")


def headless(exe: str, version: str | None, out: Path, ai: bool) -> None:
    log(f"Headless test: {exe}")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg, lib = out / "config", out / "library"
    proc = subprocess.Popen([exe, "--no-browser", "--port", str(port), "--config-dir", str(cfg), "--library", str(lib)])
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, timeout=300) as c:
            config = None
            for _ in range(90):
                if proc.poll() is not None:
                    show_log(cfg)
                    fail(f"app exited during start-up with code {proc.returncode}")
                try:
                    config = c.get("/api/config", timeout=2).json()
                    break
                except Exception:
                    time.sleep(1)
            check(config is not None, "server answers")
            if version:
                check(config["version"] == version, f"reports version {version} (got {config['version']})")
            check(config["library"] and config["library"]["tools"] == 0, "uses the given library folder")

            diag = c.get("/api/diagnostics").json()
            (out / "diagnostics.json").write_text(json.dumps(diag, indent=1))
            libs = diag["libraries"]
            for name in ("opencv", "numpy", "shapely", "manifold3d", "pillow_heif", "onnxruntime"):
                check(bool(libs.get(name)), f"bundled library loads: {name} {libs.get(name) or libs.get(name + '_error')}")

            for path in ("/", "/static/app.js", "/static/style.css", "/static/vendor/three.module.min.js", "/markers?paper=a4"):
                check(c.get(path).status_code == 200, f"serves {path}")

            sid = c.post("/api/upload", files={"file": ("photo.jpg", test_photo(), "image/jpeg")}).json()["id"]
            traced = c.post("/api/trace", json={"id": sid}).json()
            check(sizes_match(traced.get("tools", []), EXPECTED), f"classic trace finds the 3 tools at true size: {sizes(traced.get('tools', []))}")

            if ai:
                res = c.post("/api/trace", json={"id": sid, "method": "ai"})
                body = res.json()
                check(res.status_code == 200, f"AI trace runs (model download + ONNX Runtime): {body.get('detail', 'ok')}")
                found = sizes(body["tools"])
                # The AI model is not expected to catch the flat grey washer; the two real tools must be right.
                check(len(found) >= 2 and all(any(abs(f[0] - e[0]) <= TOL and abs(f[1] - e[1]) <= TOL for f in found) for e in EXPECTED if e != (30.0, 30.0)),
                      f"AI trace finds the screwdriver and hex key at true size: {found}")

            new = [{"points": t["points"], "name": f"Tool {i}", "tags": ["test"], "thickness": 6} for i, t in enumerate(traced["tools"])]
            saved = c.post("/api/tools", json={"session_id": sid, "tools": new}).json()
            check(len(saved) == 3 and c.get(f"/api/tools/{saved[0]['id']}/photo.jpg").status_code == 200, "saves tools with photos to the library")
            items = [{"kind": "tool", "tool_id": t["id"], "x": 60, "y": 60, "rot": 0, "depth": None} for t in saved]
            rec = c.post("/api/bins", json={"name": "Test bin", "settings": {"nx": 4, "ny": 4}, "items": items}).json()
            check(any(b["id"] == rec["id"] for b in c.get("/api/bins").json()), "saves a bin")
            check((lib / "tools" / saved[0]["id"] / "tool.json").exists() and (lib / "bins" / f"{rec['id']}.json").exists(), "library files are on disk")

            pockets = [{"points": [[x - 30, y - 40] for x, y in t["points"]], "depth": 8} for t in traced["tools"]]
            res = c.post("/api/bin", json={"nx": 4, "ny": 4, "pockets": pockets, "lip": True, "magnets": True})
            info = json.loads(res.headers.get("x-bin-info", "{}"))
            check(res.status_code == 200 and len(res.content) == 84 + 50 * info.get("triangles", -1) and not info.get("clipped"),
                  f"builds a valid STL ({len(res.content)} bytes, {info.get('triangles')} triangles)")
            (out / "test_bin.stl").write_bytes(res.content)
    finally:
        stop(proc)
        show_log(cfg)


def screenshot(path: Path) -> None:
    """Best effort: a picture of the screen while the window is up."""
    try:
        if sys.platform == "darwin":
            cmd = ["screencapture", "-x", str(path)]
        elif sys.platform == "win32":
            ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; $b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
                  "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; $g=[System.Drawing.Graphics]::FromImage($bmp); "
                  f"$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size); $bmp.Save('{path}')")
            cmd = ["powershell", "-NoProfile", "-Command", ps]
        else:
            cmd = ["import", "-window", "root", str(path)]
        subprocess.run(cmd, timeout=30, check=True, capture_output=True)
        log(f"  screenshot saved: {path}")
    except Exception as e:
        log(f"  (no screenshot: {e})")


def window(exe: str, version: str | None, out: Path) -> None:
    log(f"Window test: {exe}")
    result_file, cfg = out / "window.json", out / "config-window"
    result_file.unlink(missing_ok=True)
    env = dict(os.environ)
    if sys.platform.startswith("linux"):
        env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")  # CI containers lack the sandbox's requirements
    proc = subprocess.Popen([exe, "--window-test", str(result_file), "--config-dir", str(cfg)], env=env)
    try:
        deadline = time.time() + 120
        while not result_file.exists() and proc.poll() is None and time.time() < deadline:
            time.sleep(0.5)
        if not result_file.exists():
            show_log(cfg)
            fail(f"the window never reported back (exit code {proc.poll()})")
        time.sleep(1.5)  # let the page paint
        screenshot(out / "window.png")
        result = json.loads(result_file.read_text())
        log(f"  window reported: {result}")
        check(result.get("ok") is True, "desktop window opens and the UI loads inside it")
        check(result.get("native") is True, "runs in desktop-window mode (native dialogs available)")
        if version:
            check(result.get("version") == f"v{version}", f"window shows version v{version}")
        if not result.get("webgl"):
            log("  warning: WebGL is not available in this window (3D preview would not render here)")
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            fail("the window did not close")
        check(proc.returncode == 0, "app exits cleanly after the window closes")
    finally:
        stop(proc)
        show_log(cfg)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", required=True, help="path to the built Gridfinity Custom Bin executable")
    ap.add_argument("--version", help="version the app must report")
    ap.add_argument("--out", default="test-output", help="folder for results, logs and the screenshot")
    ap.add_argument("--ai", action="store_true")
    ap.add_argument("--window", action="store_true")
    args = ap.parse_args()

    exe = str(Path(args.exe).resolve())
    if not Path(exe).exists():
        fail(f"{exe} does not exist")
    out = Path(args.out).resolve()
    if out.exists():
        import shutil

        shutil.rmtree(out)
    out.mkdir(parents=True)

    headless(exe, args.version, out, args.ai)
    if args.window:
        window(exe, args.version, out)
    log("PASS")


if __name__ == "__main__":
    main()
