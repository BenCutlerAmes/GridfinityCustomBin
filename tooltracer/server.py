"""Local app server: trace tools from photos, keep a tool library, build Gridfinity bins."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import uuid
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from . import __version__, gridfinity, rectify, segment
from .library import Library, LibraryError, config_dir, default_library_path, load_config, save_config

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # HEIC support is optional
    pass

STATIC = Path(__file__).parent / "static"
MAX_PHOTO_PX = 3000
MAX_SESSIONS = 6

app = FastAPI(title="Tooltracer")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_sessions: "OrderedDict[str, dict]" = OrderedDict()


class AppState:
    cfg_dir: Path = config_dir()
    library: Library | None = None
    library_pinned = False  # set by --library: use it for this run without touching the saved config
    window = None  # pywebview window when running as a desktop app


STATE = AppState()


def init_state(cfg_dir: str | None = None, library: str | None = None) -> None:
    STATE.cfg_dir = config_dir(cfg_dir)
    STATE.library, STATE.library_pinned = None, False
    if library:
        STATE.library, STATE.library_pinned = Library(library), True
        return
    path = load_config(STATE.cfg_dir).get("library_path")
    if path and Path(path).is_dir():
        try:
            STATE.library = Library(path)
        except LibraryError:
            STATE.library = None


def _lib() -> Library:
    if STATE.library is None:
        raise HTTPException(409, "Choose a library folder first.")
    return STATE.library


def _session(sid: str) -> dict:
    if sid not in _sessions:
        raise HTTPException(404, "Photo not found - upload it again.")
    return _sessions[sid]


def _jpeg(img: np.ndarray, quality: int = 88) -> Response:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _paper(name: str, custom: list[float] | None) -> tuple[float, float]:
    if name == "custom":
        if not custom or len(custom) != 2 or min(custom) < 20 or max(custom) > 2000:
            raise HTTPException(400, "Custom sheet size must be two values between 20 and 2000 mm.")
        return float(custom[0]), float(custom[1])
    if name not in rectify.PAPER_SIZES_MM:
        raise HTTPException(400, f"Unknown paper size: {name}")
    return rectify.PAPER_SIZES_MM[name]


@app.exception_handler(LibraryError)
async def _library_error(request, exc: LibraryError):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/config")
def config():
    return {
        "version": __version__,
        "papers": {k: list(v) for k, v in rectify.PAPER_SIZES_MM.items()},
        "ai_installed": importlib.util.find_spec("onnxruntime") is not None,
        "ai_model_downloaded": segment.MODEL_PATH.exists(),
        "grid": gridfinity.GRID,
        "lip_thickness": gridfinity.LIP_T,
        "native": STATE.window is not None,
        "library": STATE.library.summary() if STATE.library else None,
        "default_library_path": str(default_library_path()),
    }


# ---------- photos + tracing ----------


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    try:
        pil = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception:
        raise HTTPException(400, "That file could not be read as an image.")
    if max(pil.size) > MAX_PHOTO_PX:
        pil.thumbnail((MAX_PHOTO_PX, MAX_PHOTO_PX), Image.LANCZOS)
    img = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    sid = uuid.uuid4().hex[:12]
    _sessions[sid] = {"photo": img, "rect": None}
    while len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)
    return {"id": sid, "width": img.shape[1], "height": img.shape[0]}


@app.get("/api/photo/{sid}.jpg")
def photo(sid: str):
    return _jpeg(_session(sid)["photo"])


@app.get("/api/rectified/{sid}.jpg")
def rectified(sid: str):
    rect = _session(sid)["rect"]
    if rect is None:
        raise HTTPException(404, "Not traced yet.")
    return _jpeg(rect)


class TraceRequest(BaseModel):
    id: str
    reference: str = "paper"  # paper | markers
    paper: str = "letter"
    custom_size: list[float] | None = None
    corners: list[list[float]] | None = None  # manual paper corners in photo pixels
    method: str = "classic"  # classic | ai
    sensitivity: float = 50.0
    min_area: float = 80.0


@app.post("/api/trace")
def trace(req: TraceRequest):
    s = _session(req.id)
    img = s["photo"]
    paper = _paper(req.paper, req.custom_size)
    warning, ignore = None, None
    try:
        if req.reference == "markers":
            rect, w_mm, h_mm, ignore, corners = rectify.rectify_markers(img, paper)
        else:
            if req.corners and len(req.corners) == 4:
                corners = np.array(req.corners, dtype=np.float32)
            else:
                try:
                    corners = rectify.detect_paper(img)
                except rectify.RectifyError as e:
                    corners = rectify.default_corners(img)
                    warning = str(e)
            corners = rectify.order_corners(corners)
            rect, w_mm, h_mm = rectify.rectify_paper(img, corners, paper)
    except rectify.RectifyError as e:
        raise HTTPException(422, str(e))
    s["rect"] = rect
    s["source"] = {"reference": req.reference, "paper": req.paper, "method": req.method}
    tools = []
    if warning is None:
        try:
            tools = segment.find_tools(rect, req.method, req.sensitivity, req.min_area, ignore)
        except segment.SegmentError as e:
            raise HTTPException(422, str(e))
        if not tools:
            warning = "No tools found. Try raising the sensitivity, or switch detection mode."
    return {
        "corners": np.asarray(corners).round(1).tolist(),
        "sheet_mm": [w_mm, h_mm],
        "tools": tools,
        "warning": warning,
    }


# ---------- library folder ----------


class LibraryPath(BaseModel):
    path: str


@app.post("/api/library")
def set_library(req: LibraryPath):
    path = req.path.strip().strip('"')
    if not path:
        raise HTTPException(400, "Enter a folder path.")
    STATE.library = Library(path)
    STATE.library_pinned = False
    cfg = load_config(STATE.cfg_dir)
    cfg["library_path"] = str(STATE.library.root)
    save_config(STATE.cfg_dir, cfg)
    return STATE.library.summary()


@app.post("/api/library/browse")
def browse_library():
    """Native folder picker (desktop window only). Returns {"path": str | None}."""
    if STATE.window is None:
        raise HTTPException(400, "Folder browsing is only available in the desktop window; type the path instead.")
    import webview

    start = str(STATE.library.root.parent) if STATE.library else str(default_library_path().parent)
    result = STATE.window.create_file_dialog(webview.FOLDER_DIALOG, directory=start)
    return {"path": result[0] if result else None}


@app.post("/api/library/reveal")
def reveal_library():
    root = str(_lib().root)
    try:
        if sys.platform == "win32":
            os.startfile(root)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", root])
        else:
            subprocess.Popen(["xdg-open", root])
    except Exception as e:
        raise HTTPException(500, f"Could not open the folder: {e}")
    return {"ok": True}


# ---------- tools ----------


class NewTool(BaseModel):
    points: list[list[float]]  # outline on the flattened sheet, mm
    name: str = ""
    tags: list[str] | str = []
    thickness: float | None = Field(None, ge=0, le=200)


class NewTools(BaseModel):
    session_id: str
    tools: list[NewTool]


class ToolPatch(BaseModel):
    name: str | None = None
    tags: list[str] | str | None = None
    thickness: float | None = Field(None, ge=0, le=200)


@app.get("/api/tools")
def list_tools():
    return _lib().list_tools()


@app.post("/api/tools")
def add_tools(req: NewTools):
    lib = _lib()
    s = _session(req.session_id)
    if s["rect"] is None:
        raise HTTPException(400, "Trace the photo first.")
    return [lib.add_tool(t.points, s["rect"], t.name, t.tags, t.thickness, s.get("source")) for t in req.tools]


@app.patch("/api/tools/{tool_id}")
def update_tool(tool_id: str, patch: ToolPatch):
    return _lib().update_tool(tool_id, patch.model_dump(exclude_unset=True))


@app.delete("/api/tools/{tool_id}")
def delete_tool(tool_id: str):
    _lib().delete_tool(tool_id)
    return {"ok": True}


@app.get("/api/tools/{tool_id}/photo.jpg")
def tool_photo(tool_id: str):
    return FileResponse(_lib().tool_photo(tool_id), media_type="image/jpeg")


# ---------- saved bins ----------


class BinRecord(BaseModel):
    name: str = ""
    settings: dict = {}
    items: list[dict] = []


@app.get("/api/bins")
def list_bins():
    return _lib().list_bins()


@app.post("/api/bins")
def create_bin(rec: BinRecord):
    return _lib().save_bin(rec.model_dump())


@app.put("/api/bins/{bin_id}")
def update_bin(bin_id: str, rec: BinRecord):
    return _lib().save_bin(rec.model_dump(), bin_id)


@app.delete("/api/bins/{bin_id}")
def delete_bin(bin_id: str):
    _lib().delete_bin(bin_id)
    return {"ok": True}


# ---------- STL ----------


class Pocket(BaseModel):
    points: list[list[float]]
    depth: float = Field(gt=0, le=200)


class Circle(BaseModel):
    x: float
    y: float
    d: float = Field(gt=0, le=200)
    depth: float = Field(gt=0, le=200)


class BinRequest(BaseModel):
    nx: int = Field(ge=1, le=12)
    ny: int = Field(ge=1, le=12)
    pockets: list[Pocket] = []
    circles: list[Circle] = []
    clearance: float = Field(0.5, ge=0, le=5)
    height_units: int | None = Field(None, ge=1, le=30)
    magnets: bool = False
    lip: bool = False
    name: str = ""  # optional, used for the file name


def _build(req: BinRequest):
    solid, info = gridfinity.build_bin(
        req.nx,
        req.ny,
        [p.model_dump() for p in req.pockets],
        [c.model_dump() for c in req.circles],
        req.clearance,
        req.height_units,
        req.magnets,
        req.lip,
    )
    label = re.sub(r"[^A-Za-z0-9 _-]+", "", req.name).strip().replace(" ", "_")[:40] or "tools"
    name = f"gridfinity_{req.nx}x{req.ny}x{info['height_units']}_{label}{'_lip' if req.lip else ''}.stl"
    return gridfinity.to_stl(solid), info, name


@app.post("/api/bin")
def make_bin(req: BinRequest):
    stl, info, name = _build(req)
    return Response(
        stl,
        media_type="model/stl",
        headers={
            "X-Bin-Info": json.dumps(info),
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/bin/save")
def save_bin_stl(req: BinRequest):
    """Desktop window only: ask where to save with a native dialog and write the STL there."""
    if STATE.window is None:
        raise HTTPException(400, "Only available in the desktop window.")
    import webview

    stl, info, name = _build(req)
    cfg = load_config(STATE.cfg_dir)
    start = cfg.get("last_export_dir") or str(Path.home())
    result = STATE.window.create_file_dialog(
        webview.SAVE_DIALOG, directory=start, save_filename=name, file_types=("STL files (*.stl)",)
    )
    if not result:
        return {"path": None, "info": info}
    path = Path(result if isinstance(result, str) else result[0])
    if path.suffix.lower() != ".stl":
        path = path.with_suffix(".stl")
    path.write_bytes(stl)
    cfg["last_export_dir"] = str(path.parent)
    save_config(STATE.cfg_dir, cfg)
    return {"path": str(path), "info": info}


# ---------- marker sheet ----------


@app.get("/api/marker/{marker_id}.png")
def marker(marker_id: int):
    if marker_id not in rectify.MARKER_IDS:
        raise HTTPException(404)
    return Response(rectify.marker_png(marker_id), media_type="image/png")


@app.get("/markers", response_class=HTMLResponse)
def marker_sheet(paper: str = "letter"):
    w, h = sorted(_paper(paper, None))
    imgs = "".join(
        f'<img src="/api/marker/{i}.png" style="left:{q[0][0]}mm;top:{q[0][1]}mm">'
        for i, q in rectify.marker_positions_mm(w, h).items()
    )
    s = rectify.MARKER_SIZE_MM
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Tooltracer marker sheet ({paper})</title>
<style>
@page {{ size: {w}mm {h}mm; margin: 0; }}
html, body {{ margin: 0; padding: 0; }}
.page {{ position: relative; width: {w}mm; height: {h}mm; overflow: hidden; background: #fff; font: 3mm sans-serif; color: #999; }}
.page img {{ position: absolute; width: {s}mm; height: {s}mm; image-rendering: pixelated; }}
.ruler {{ position: absolute; left: {(w - 100) / 2}mm; bottom: 14mm; width: 100mm; height: 3mm;
  border: 0.2mm solid #999; border-top: none; box-sizing: border-box; }}
.note {{ position: absolute; left: 0; right: 0; bottom: 8mm; text-align: center; }}
.help {{ font: 14px sans-serif; color: #222; padding: 12px 16px; background: #ffe9a8; }}
@media print {{ .help {{ display: none; }} }}
</style></head><body>
<div class="help">Print this page at <b>100% / Actual size</b> (not "Fit to page"), margins off. Then check the ruler at the
bottom is exactly 100 mm. Lay tools in the middle, keep all four markers visible. <button onclick="print()">Print</button></div>
<div class="page">{imgs}<div class="ruler"></div><div class="note">100 mm check ruler &middot; Tooltracer marker sheet &middot; {paper}</div></div>
</body></html>"""


def main():
    from .launcher import main as launch

    launch()


if __name__ == "__main__":
    main()
