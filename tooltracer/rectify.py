"""Turn a photo into a flat, true-scale, top-down image of the reference sheet.

Two references are supported:
  * a plain sheet of paper of known size (its four corners give scale + perspective)
  * a printed marker sheet with four ArUco markers at known positions
"""
from __future__ import annotations

import cv2
import numpy as np

PX_PER_MM = 5.0  # resolution of the rectified image (0.2 mm per pixel)

PAPER_SIZES_MM = {  # (short side, long side)
    "letter": (215.9, 279.4),
    "a4": (210.0, 297.0),
    "legal": (215.9, 355.6),
    "tabloid": (279.4, 431.8),
    "a3": (297.0, 420.0),
}

# Marker sheet layout: one 4x4 ArUco marker in each corner of the page.
MARKER_SIZE_MM = 20.0
MARKER_INSET_MM = 10.0  # from page edge to the marker's outer edge
MARKER_IDS = (0, 1, 2, 3)  # top-left, top-right, bottom-right, bottom-left (portrait page)


class RectifyError(Exception):
    pass


def _aruco_dict():
    return cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)


def marker_png(marker_id: int, px: int = 400) -> bytes:
    img = cv2.aruco.generateImageMarker(_aruco_dict(), marker_id, px)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def marker_positions_mm(page_w: float, page_h: float) -> dict[int, np.ndarray]:
    """Corner coordinates (TL, TR, BR, BL) of each marker on a portrait page, in mm."""
    s, m = MARKER_SIZE_MM, MARKER_INSET_MM
    origins = {
        0: (m, m),
        1: (page_w - m - s, m),
        2: (page_w - m - s, page_h - m - s),
        3: (m, page_h - m - s),
    }
    return {
        i: np.array([[x, y], [x + s, y], [x + s, y + s], [x, y + s]], dtype=np.float32)
        for i, (x, y) in origins.items()
    }


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as TL, TR, BR, BL (clockwise on screen, y pointing down)."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]  # clockwise on screen, starting somewhere
    start = int(np.argmin(pts.sum(axis=1)))  # top-left has the smallest x+y
    return np.roll(pts, -start, axis=0)


def detect_paper(img: np.ndarray) -> np.ndarray:
    """Find the sheet of paper: the largest bright quadrilateral. Returns 4 corners (px)."""
    h, w = img.shape[:2]
    scale = 1000.0 / max(h, w)
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img.copy()
    if scale >= 1:
        scale = 1.0
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
    light = cv2.GaussianBlur(lab[..., 0], (7, 7), 0)
    # Paper is bright and nearly colourless.
    chroma = cv2.absdiff(lab[..., 1], 128).astype(np.int16) + cv2.absdiff(lab[..., 2], 128).astype(np.int16)
    _, bright = cv2.threshold(light, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright[chroma > 40] = 0
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise RectifyError("Could not find the sheet of paper. Drag the corner handles onto it manually.")
    best = max(contours, key=cv2.contourArea)
    if cv2.contourArea(best) < 0.08 * small.shape[0] * small.shape[1]:
        raise RectifyError("Could not find the sheet of paper. Drag the corner handles onto it manually.")
    # A frame with no visible sheet edges (blank wall, or paper filling the whole photo) thresholds
    # to "everything is paper"; there is nothing to take scale from.
    inside = np.zeros_like(bright)
    cv2.drawContours(inside, [best], -1, 255, -1)
    outside = inside == 0
    if outside.mean() < 0.03 or float(light[inside > 0].mean()) - float(light[outside].mean()) < 15:
        raise RectifyError("Could not see the edges of the sheet. Keep the whole sheet in frame on a darker surface.")
    hull = cv2.convexHull(best)  # tools hanging over the paper edge only dent it; the hull ignores dents
    quad = None
    peri = cv2.arcLength(hull, True)
    for eps in np.linspace(0.01, 0.08, 15):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            quad = approx.reshape(4, 2).astype(np.float32)
            break
    if quad is None:
        quad = cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)
    return order_corners(quad / scale)


def default_corners(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    return np.array([[w * 0.15, h * 0.15], [w * 0.85, h * 0.15], [w * 0.85, h * 0.85], [w * 0.15, h * 0.85]], np.float32)


def rectify_paper(img: np.ndarray, corners: np.ndarray, paper: tuple[float, float]):
    """Warp so the paper fills the frame. Returns (image, width_mm, height_mm)."""
    c = order_corners(corners)
    top = np.linalg.norm(c[1] - c[0]) + np.linalg.norm(c[2] - c[3])
    side = np.linalg.norm(c[3] - c[0]) + np.linalg.norm(c[2] - c[1])
    short, long_ = sorted(paper)
    w_mm, h_mm = (long_, short) if top >= side else (short, long_)
    W, H = int(round(w_mm * PX_PER_MM)), int(round(h_mm * PX_PER_MM))
    dst = np.array([[0, 0], [W, 0], [W, H], [0, H]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(c, dst)
    return cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_AREA, borderValue=(255, 255, 255)), w_mm, h_mm


def rectify_markers(img: np.ndarray, paper: tuple[float, float]):
    """Warp using the printed marker sheet. Returns (image, width_mm, height_mm, ignore_mask, page_corners_px)."""
    page_w, page_h = sorted(paper)  # the sheet is designed in portrait
    detector = cv2.aruco.ArucoDetector(_aruco_dict(), cv2.aruco.DetectorParameters())
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    found, ids, _ = detector.detectMarkers(gray)
    ids = [] if ids is None else ids.flatten().tolist()
    ref = marker_positions_mm(page_w, page_h)
    src, dst = [], []
    for corners, mid in zip(found, ids):
        if mid in ref:
            src.append(corners.reshape(4, 2))
            dst.append(ref[mid] * PX_PER_MM)
    if len(src) < 3:
        raise RectifyError(
            f"Found {len(src)} of 4 markers; need at least 3. Make sure the whole marker sheet is in frame, "
            "evenly lit, and that no tool covers a marker."
        )
    src = np.concatenate(src).astype(np.float32)
    dst = np.concatenate(dst).astype(np.float32)
    M, _ = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if M is None:
        raise RectifyError("Could not solve the sheet position from the markers.")
    W, H = int(round(page_w * PX_PER_MM)), int(round(page_h * PX_PER_MM))
    rect = cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_AREA, borderValue=(255, 255, 255))
    ignore = np.zeros((H, W), np.uint8)
    pad = 3.0
    for quad in ref.values():
        x0, y0 = (quad[0] - pad) * PX_PER_MM
        x1, y1 = (quad[2] + pad) * PX_PER_MM
        cv2.rectangle(ignore, (int(x0), int(y0)), (int(x1), int(y1)), 255, -1)
    page = np.array([[[0, 0], [W, 0], [W, H], [0, H]]], dtype=np.float32)
    page_px = cv2.perspectiveTransform(page, np.linalg.inv(M)).reshape(4, 2)
    return rect, page_w, page_h, ignore, page_px
