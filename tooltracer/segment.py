"""Find tools in a rectified (flat, true-scale) image and return their outlines in mm.

Two detectors:
  * classic - OpenCV only: anything that differs from the white sheet.
  * ai      - IS-Net salient-object segmentation run locally through ONNX Runtime.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import platformdirs
from shapely.geometry import Polygon
from shapely.validation import make_valid

from .rectify import PX_PER_MM

MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx"
MODEL_NAME = "isnet-general-use.onnx"


def _model_path() -> Path:
    """Running from source: models/ beside the code. Packaged app: the per-user data folder,
    because the install folder may be read-only and is replaced on upgrade."""
    if getattr(sys, "frozen", False):
        return Path(platformdirs.user_data_dir("Tooltracer", False)) / "models" / MODEL_NAME
    return Path(__file__).resolve().parent.parent / "models" / MODEL_NAME


MODEL_PATH = _model_path()
EDGE_MARGIN_MM = 1.5

_session = None


class SegmentError(Exception):
    pass


def _mm(v: float) -> int:
    return max(1, int(round(v * PX_PER_MM)))


def _ellipse(mm: float) -> np.ndarray:
    k = _mm(mm) | 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def classic_mask(img: np.ndarray, sensitivity: float = 50.0) -> np.ndarray:
    """Mask of everything that is not blank paper. sensitivity 0..100, higher catches fainter things."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    light, a, b = lab[..., 0], lab[..., 1], lab[..., 2]

    # Estimate the blank-paper brightness everywhere (flattens uneven lighting): work at low
    # resolution, erase dark things with a closing far wider than any tool, then blur.
    f = 0.125
    small = cv2.resize(light, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    k = max(3, int(60 * PX_PER_MM * f)) | 1
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 3)
    bg = cv2.resize(bg, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)

    darker = np.clip(bg - light, 0, None)
    chroma = np.hypot(a - np.median(a), b - np.median(b))
    score = cv2.GaussianBlur(darker + 1.5 * chroma, (0, 0), 1.0)
    s8 = np.clip(score, 0, 255).astype(np.uint8)

    otsu, _ = cv2.threshold(s8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Otsu sits between paper and the darkest tools, which misses mid-grey (steel) tools.
    # Slide the threshold between the paper's noise floor and Otsu instead.
    med = float(np.median(score))
    mad = float(np.median(np.abs(score - med)))
    floor = max(8.0, med + 8.0 * 1.4826 * mad)
    t = (1.0 - np.clip(sensitivity, 0, 100) / 100.0) ** 1.5  # 1 .. 0, 0.35 at the default
    thr = floor + max(0.0, otsu - floor) * t * 1.6
    mask = (score > thr).astype(np.uint8) * 255
    return mask


def _load_session():
    global _session
    if _session is not None:
        return _session
    try:
        import onnxruntime as ort
    except ImportError as e:
        raise SegmentError("AI detection needs ONNX Runtime. Install it with:  uv sync --extra ai") from e
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MODEL_PATH.with_suffix(".part")
        try:
            # Use the bundled CA certificates: a packaged app cannot rely on the system store.
            import shutil
            import ssl

            import certifi

            ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(MODEL_URL, context=ctx, timeout=60) as resp, open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f, 1 << 20)
            tmp.replace(MODEL_PATH)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise SegmentError(f"Could not download the AI model (one-time, ~170 MB): {e}") from e
    _session = ort.InferenceSession(str(MODEL_PATH), providers=ort.get_available_providers())
    return _session


def ai_mask(img: np.ndarray, sensitivity: float = 50.0) -> np.ndarray:
    sess = _load_session()
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    x = cv2.resize(rgb, (1024, 1024), interpolation=cv2.INTER_AREA).astype(np.float32)
    x = x / max(float(x.max()), 1e-6) - 0.5
    x = x.transpose(2, 0, 1)[None]
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    lo, hi = float(out.min()), float(out.max())
    prob = (out - lo) / max(hi - lo, 1e-6)
    prob = cv2.resize(prob, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
    thr = 0.85 - 0.7 * (np.clip(sensitivity, 0, 100) / 100.0)  # 0.85 .. 0.15, 0.5 at the default
    return (prob > thr).astype(np.uint8) * 255


def mask_to_tools(mask: np.ndarray, min_area_mm2: float = 80.0, ignore: np.ndarray | None = None) -> list[dict]:
    """Clean a raw mask and convert each blob into a smooth polygon in mm (y pointing down)."""
    h, w = mask.shape
    mask = mask.copy()
    if ignore is not None:
        mask[ignore > 0] = 0
    m = _mm(EDGE_MARGIN_MM)
    mask[:m], mask[-m:], mask[:, :m], mask[:, -m:] = 0, 0, 0, 0

    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _ellipse(1.6))
    # Fill interiors: shiny tools often only show up as a closed rim.
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 255, -1)
    filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN, _ellipse(0.8))
    contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

    tools = []
    for c in contours:
        if len(c) < 8:
            continue
        pts = c.reshape(-1, 2).astype(np.float64) / PX_PER_MM
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = make_valid(poly)
        # Round off pixel stair-steps without losing real detail.
        poly = poly.buffer(0.4, join_style="round").buffer(-0.4, join_style="round").simplify(0.12)
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        if poly.geom_type != "Polygon" or poly.is_empty or poly.area < min_area_mm2:
            continue
        ring = np.asarray(poly.exterior.coords)[:-1]
        minx, miny, maxx, maxy = poly.bounds
        tools.append(
            {
                "points": np.round(ring, 3).tolist(),
                "area": round(poly.area, 1),
                "width": round(maxx - minx, 1),
                "height": round(maxy - miny, 1),
            }
        )
    tools.sort(key=lambda t: -t["area"])
    return tools


def find_tools(img, method="classic", sensitivity=50.0, min_area_mm2=80.0, ignore=None) -> list[dict]:
    mask = ai_mask(img, sensitivity) if method == "ai" else classic_mask(img, sensitivity)
    return mask_to_tools(mask, min_area_mm2, ignore)
