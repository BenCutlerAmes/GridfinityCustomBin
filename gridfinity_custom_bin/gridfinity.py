"""Build a Gridfinity bin with tool-shaped pockets and export it as a binary STL.

Coordinates: the caller works in "layout" millimetres with the origin at the top-left of
the bin's grid footprint and y pointing DOWN (like the photo). The model is built with
y pointing UP, so the layout is flipped here; viewed from above the pockets match the photo.
"""
from __future__ import annotations

import math

import manifold3d as m3
import numpy as np
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.validation import make_valid

GRID = 42.0
GAP = 0.25  # clearance each side between bin and grid cell
HEIGHT_UNIT = 7.0
CORNER_R = 3.75
BASE_H = 4.75  # height of the stepped foot profile
MIN_FLOOR = 1.0  # solid material between the deepest pocket and the feet
MIN_WALL = 1.2
MAGNET_D = 6.5
MAGNET_H = 2.4
MAGNET_OFFSET = 13.0

# Foot profile per grid cell: (z, half-width, corner radius)
FOOT_PROFILE = [
    (0.0, 17.8, 0.8),
    (0.8, 18.6, 1.6),
    (2.6, 18.6, 1.6),
    (BASE_H, 20.75, CORNER_R),
]

# Stacking lip: a rim around the top whose inner face mirrors the foot profile, so another
# bin's feet drop into it. (height above the bin top, wall thickness at that height)
LIP_PROFILE = [
    (0.0, 2.6),
    (0.7, 1.9),
    (2.5, 1.9),
    (4.4, 0.0),
]
LIP_T = LIP_PROFILE[0][1]  # rim thickness where it meets the bin top
LIP_H = 3.9  # the profile is cut off here, leaving a 0.5 mm flat instead of a knife edge


def _rounded_rect(cx, cy, hx, hy, r, seg=12) -> np.ndarray:
    pts = []
    for (sx, sy, a0) in ((1, 1, 0.0), (-1, 1, 90.0), (-1, -1, 180.0), (1, -1, 270.0)):
        ox, oy = cx + sx * (hx - r), cy + sy * (hy - r)
        for i in range(seg + 1):
            a = math.radians(a0 + 90.0 * i / seg)
            pts.append((ox + r * math.cos(a), oy + r * math.sin(a)))
    return np.array(pts)


def _ring3(cx, cy, half, r, z) -> np.ndarray:
    p = _rounded_rect(cx, cy, half, half, r)
    return np.column_stack([p, np.full(len(p), z)])


def _foot(cx, cy) -> m3.Manifold:
    rings = [_ring3(cx, cy, half, r, z) for z, half, r in FOOT_PROFILE]
    lower = m3.Manifold.hull_points(np.vstack(rings[:3]))  # chamfer + straight section (convex)
    upper = m3.Manifold.hull_points(np.vstack(rings[2:]))  # top chamfer
    return lower + upper


def _lip(width, depth_y, z0) -> m3.Manifold:
    """Rim around the whole bin, sitting on the top surface at z0."""
    cx, cy, hx, hy = width / 2, depth_y / 2, width / 2 - GAP, depth_y / 2 - GAP
    rim = m3.CrossSection([_rounded_rect(cx, cy, hx, hy, CORNER_R)]).extrude(LIP_H).translate((0, 0, z0))
    rings = []
    for h, t in LIP_PROFILE:
        p = _rounded_rect(cx, cy, hx - t, hy - t, max(CORNER_R - t, 0.05))
        rings.append(np.column_stack([p, np.full(len(p), z0 + h)]))
    # The opening widens, runs straight, then widens again: two convex pieces.
    opening = m3.Manifold.hull_points(np.vstack(rings[:3])) + m3.Manifold.hull_points(np.vstack(rings[2:]))
    return rim - opening


def _cross_section(geom) -> m3.CrossSection:
    polys = [geom] if geom.geom_type == "Polygon" else [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]
    contours = []
    for p in polys:
        if p.is_empty:
            continue
        p = orient(p, 1.0)
        contours.append(np.asarray(p.exterior.coords)[:-1])
        contours.extend(np.asarray(i.coords)[:-1] for i in p.interiors)
    return m3.CrossSection(contours, m3.FillRule.Positive)


def required_units(max_depth: float) -> int:
    return max(2, math.ceil((max_depth + BASE_H + MIN_FLOOR) / HEIGHT_UNIT - 1e-9))


def build_bin(
    nx: int,
    ny: int,
    pockets: list[dict],
    circles: list[dict] | None = None,
    clearance: float = 0.5,
    height_units: int | None = None,
    magnets: bool = False,
    lip: bool = False,
):
    """pockets: [{"points": [[x, y], ...], "depth": mm}]   circles: [{"x", "y", "d", "depth"}]

    Returns (manifold, info dict).
    """
    circles = circles or []
    width, depth_y = nx * GRID, ny * GRID
    depths = [float(p["depth"]) for p in pockets] + [float(c["depth"]) for c in circles]
    max_depth = max(depths, default=0.0)
    need = required_units(max_depth)
    units = max(need, int(height_units or 0))
    H = units * HEIGHT_UNIT

    m3.set_circular_segments(64)
    outline = _rounded_rect(width / 2, depth_y / 2, width / 2 - GAP, depth_y / 2 - GAP, CORNER_R)
    body = m3.CrossSection([outline]).extrude(H - BASE_H).translate((0, 0, BASE_H))
    feet = [_foot(GRID * (i + 0.5), GRID * (j + 0.5)) for i in range(nx) for j in range(ny)]
    solid = m3.Manifold.batch_boolean([body, *feet], m3.OpType.Add)

    # Group cut-outs by depth so overlapping shapes of equal depth merge cleanly.
    # With a lip, pockets must stay inside the rim so they don't cut through it.
    wall = LIP_T if lip else MIN_WALL
    interior = Polygon(_rounded_rect(width / 2, depth_y / 2, width / 2 - GAP - wall, depth_y / 2 - GAP - wall, CORNER_R - wall))
    by_depth: dict[float, list] = {}
    clipped = False
    for p in pockets:
        pts = np.asarray(p["points"], dtype=float)
        if len(pts) < 3:
            continue
        pts = np.column_stack([pts[:, 0], depth_y - pts[:, 1]])
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = make_valid(poly)
        poly = poly.buffer(clearance, join_style="round")
        by_depth.setdefault(round(float(p["depth"]), 3), []).append(poly)
    for c in circles:
        by_depth.setdefault(round(float(c["depth"]), 3), []).append(
            Point(float(c["x"]), depth_y - float(c["y"])).buffer(float(c["d"]) / 2, quad_segs=24)
        )

    cuts = []
    for d, shapes in by_depth.items():
        if d <= 0:
            continue
        merged = unary_union(shapes)
        inside = merged.intersection(interior)
        if merged.area - inside.area > 0.5:
            clipped = True
        if inside.is_empty:
            continue
        cs = _cross_section(inside)
        if cs.is_empty():
            continue
        cuts.append(cs.extrude(d + 1.0).translate((0, 0, H - d)))

    if magnets:
        for i in range(nx):
            for j in range(ny):
                cx, cy = GRID * (i + 0.5), GRID * (j + 0.5)
                for sx in (-1, 1):
                    for sy in (-1, 1):
                        cuts.append(
                            m3.Manifold.cylinder(MAGNET_H + 0.5, MAGNET_D / 2).translate(
                                (cx + sx * MAGNET_OFFSET, cy + sy * MAGNET_OFFSET, -0.5)
                            )
                        )
    if cuts:
        solid = solid - m3.Manifold.batch_boolean(cuts, m3.OpType.Add)
    if lip:
        solid = solid + _lip(width, depth_y, H)
    total_h = round(H + (LIP_H if lip else 0.0), 2)

    info = {
        "nx": nx,
        "ny": ny,
        "height_units": units,
        "height_mm": H,
        "size_mm": [round(width - 2 * GAP, 2), round(depth_y - 2 * GAP, 2), total_h],
        "lip": lip,
        "raised_height": units > int(height_units or units),
        "clipped": clipped,
        "volume_cm3": round(solid.volume() / 1000.0, 1),
        "triangles": solid.num_tri(),
    }
    return solid, info


def to_stl(solid: m3.Manifold) -> bytes:
    mesh = solid.to_mesh()
    verts = np.asarray(mesh.vert_properties, dtype=np.float32)[:, :3]
    tris = verts[np.asarray(mesh.tri_verts, dtype=np.int64)]
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.where(length == 0, 1, length)
    rec = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    rec["n"], rec["v"] = normals, tris
    return b"Gridfinity Custom Bin STL".ljust(80, b" ") + np.uint32(len(tris)).tobytes() + rec.tobytes()


def fit_grid(extent_mm: float, clearance: float) -> int:
    return max(1, math.ceil((extent_mm + 2 * clearance + 2 * (GAP + MIN_WALL)) / GRID - 1e-9))
