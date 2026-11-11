"""Local web server: upload a photo, trace tools, lay them out, download a Gridfinity STL."""
from __future__ import annotations

import importlib.util
import io
import json
import uuid
import webbrowser
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from . import gridfinity, rectify, segment

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


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/config")
def config():
    return {
        "papers": {k: list(v) for k, v in rectify.PAPER_SIZES_MM.items()},
        "ai_installed": importlib.util.find_spec("onnxruntime") is not None,
        "ai_model_downloaded": segment.MODEL_PATH.exists(),
        "grid": gridfinity.GRID,
        "lip_thickness": gridfinity.LIP_T,
    }


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


@app.post("/api/bin")
def make_bin(req: BinRequest):
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
    name = f"gridfinity_{req.nx}x{req.ny}x{info['height_units']}_tools{'_lip' if req.lip else ''}.stl"
    return Response(
        gridfinity.to_stl(solid),
        media_type="model/stl",
        headers={
            "X-Bin-Info": json.dumps(info),
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


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
    import uvicorn

    import sys

    host, port = "127.0.0.1", 8765
    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(f"http://{host}:{port}")
        except Exception:
            pass
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
