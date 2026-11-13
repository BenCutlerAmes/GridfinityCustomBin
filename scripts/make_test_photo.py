"""Generate a synthetic "photo" of tools on a Letter sheet, for testing without a camera.

Usage:  uv run python scripts/make_test_photo.py [out.jpg] [--markers]
The scene contains an L-shaped hex key (100 x 40 mm legs, 8 mm thick), a 150 mm screwdriver
with a red handle, and a 30 mm washer - known sizes, so the traced output can be checked.
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gridfinity_custom_bin.rectify import marker_positions_mm, _aruco_dict  # noqa: E402

S = 8  # px per mm on the flat sheet
W_MM, H_MM = 215.9, 279.4


def mm(*v):
    return tuple(int(round(x * S)) for x in v)


def make(markers: bool) -> np.ndarray:
    sheet = np.full((int(H_MM * S), int(W_MM * S), 3), 244, np.uint8)
    shadow = np.zeros(sheet.shape[:2], np.uint8)

    def draw(fn, color):
        fn(shadow, 255, (int(1.5 * S), int(2 * S)))
        fn(sheet, color, (0, 0))

    # L-shaped hex key: long leg 100 mm down, short leg 40 mm to the right at the bottom.
    def hexkey(img, color, off):
        pts = np.array([mm(40, 60), mm(48, 60), mm(48, 152), mm(80, 152), mm(80, 160), mm(40, 160)]) + off
        cv2.fillPoly(img, [pts], color)

    draw(hexkey, (60, 58, 55))

    def driver(img, color, off):
        ox, oy = off
        cv2.rectangle(img, (mm(120)[0] + ox, mm(50)[0] + oy), (mm(126)[0] + ox, mm(140)[0] + oy), color if isinstance(color, int) else (120, 120, 125), -1)
        cv2.rectangle(img, (mm(111)[0] + ox, mm(140)[0] + oy), (mm(135)[0] + ox, mm(200)[0] + oy), color if isinstance(color, int) else (40, 40, 200), -1)

    draw(driver, None)

    def washer(img, color, off):
        c = (mm(165)[0] + off[0], mm(90)[0] + off[1])
        cv2.circle(img, c, mm(15)[0], color if isinstance(color, int) else (150, 150, 150), -1)
        if not isinstance(color, int):
            cv2.circle(img, c, mm(6)[0], (244, 244, 244), -1)

    draw(washer, None)

    # Soft shadow under the tools, then redraw tools on top.
    soft = cv2.GaussianBlur(shadow, (0, 0), 2.0 * S).astype(np.float32) / 255.0
    clean = sheet.copy()
    base = np.full_like(sheet, 244)
    shaded = (base * (1 - 0.25 * soft[..., None])).astype(np.uint8)
    tool_px = np.any(clean != 244, axis=2)
    shaded[tool_px] = clean[tool_px]
    sheet = shaded

    if markers:
        d = _aruco_dict()
        for mid, quad in marker_positions_mm(W_MM, H_MM).items():
            x, y = mm(*quad[0])
            size = mm(quad[1][0] - quad[0][0])[0]
            sheet[y : y + size, x : x + size] = cv2.cvtColor(cv2.aruco.generateImageMarker(d, mid, size), cv2.COLOR_GRAY2BGR)

    # Place the sheet on a wooden-ish table, seen slightly off-axis.
    out_w, out_h = 2400, 3000
    table = np.full((out_h, out_w, 3), (70, 100, 135), np.uint8)
    src = np.float32([[0, 0], [sheet.shape[1], 0], [sheet.shape[1], sheet.shape[0]], [0, sheet.shape[0]]])
    dst = np.float32([[420, 380], [2010, 300], [2120, 2650], [300, 2560]])
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(sheet, M, (out_w, out_h))
    mask = cv2.warpPerspective(np.full(sheet.shape[:2], 255, np.uint8), M, (out_w, out_h))
    table[mask > 0] = warped[mask > 0]

    # Uneven lighting + sensor noise.
    yy, xx = np.mgrid[0:out_h, 0:out_w].astype(np.float32)
    light = 0.78 + 0.22 * (xx / out_w) * (1 - 0.3 * yy / out_h)
    img = table.astype(np.float32) * light[..., None]
    img += np.random.default_rng(1).normal(0, 3, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0] if args else "out/test_photo.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), make("--markers" in sys.argv))
    print("wrote", out)
