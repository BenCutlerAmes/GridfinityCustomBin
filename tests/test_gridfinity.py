"""Bin geometry: valid printable solids, correct sizes, and a lip that really accepts another bin."""
import manifold3d as m3
import pytest

from tooltracer import gridfinity as G

SQUARE = [[20, 20], [60, 20], [60, 50], [20, 50]]


def build(**kw):
    args = dict(nx=2, ny=3, pockets=[{"points": SQUARE, "depth": 8}], circles=[], clearance=0.5, height_units=None, magnets=False, lip=False)
    args.update(kw)
    return G.build_bin(**args)


@pytest.mark.parametrize("opts", [{}, {"magnets": True}, {"lip": True}, {"magnets": True, "lip": True, "circles": [{"x": 40, "y": 80, "d": 20, "depth": 6}]}])
def test_bin_is_one_valid_solid(opts):
    solid, info = build(**opts)
    assert solid.status() == m3.Error.NoError
    assert not solid.is_empty() and solid.volume() > 0
    assert len(solid.decompose()) == 1  # nothing floating
    assert len(G.to_stl(solid)) == 84 + 50 * info["triangles"]


def test_footprint_and_height():
    solid, info = build()
    x0, y0, z0, x1, y1, z1 = solid.bounding_box()
    assert (round(x1 - x0, 2), round(y1 - y0, 2)) == (83.5, 125.5)  # 42 mm grid minus 0.25 mm each side
    assert z0 == pytest.approx(0) and z1 == pytest.approx(14.0)  # 8 mm pocket needs 2 units
    assert info["height_units"] == 2 and info["size_mm"] == [83.5, 125.5, 14.0]


def test_pocket_removes_material_and_clearance_enlarges_it():
    full = build(pockets=[])[0].volume()
    tight = build(clearance=0.0)[0].volume()
    loose = build(clearance=1.0)[0].volume()
    assert full - tight == pytest.approx(40 * 30 * 8, rel=0.01)
    assert loose < tight


def test_height_is_raised_to_keep_a_floor():
    _, info = build(pockets=[{"points": SQUARE, "depth": 20}], height_units=2)
    assert info["raised_height"] and info["height_units"] == 4  # 20 + 4.75 base + 1 floor -> 28 mm


def test_pocket_past_the_wall_is_trimmed_and_flagged():
    solid, info = build(pockets=[{"points": [[-5, 10], [40, 10], [40, 30], [-5, 30]], "depth": 8}])
    assert info["clipped"]
    x0, *_ = solid.bounding_box()
    assert x0 == pytest.approx(0.25)  # outer wall intact


def test_pocket_layout_matches_the_photo_orientation():
    """Layout y points down (like the photo); a pocket near the layout's top must end up at high model y."""
    solid, _ = build(pockets=[{"points": [[30, 10], [50, 10], [50, 20], [30, 20]], "depth": 8}])
    probe_top = m3.Manifold.cube((10, 4, 4)).translate((35, 126 - 17, 9))
    probe_bottom = m3.Manifold.cube((10, 4, 4)).translate((35, 13, 9))
    assert (solid ^ probe_top).volume() == pytest.approx(0, abs=1e-6)  # empty: inside the pocket
    assert (solid ^ probe_bottom).volume() == pytest.approx(160, rel=0.01)  # solid plastic


def test_stacking_lip_accepts_another_bin_and_locks_it():
    lipped, info = build(lip=True)
    assert info["size_mm"][2] == pytest.approx(14.0 + G.LIP_H)
    upper = build()[0].translate((0, 0, info["height_mm"]))
    assert (lipped ^ upper).volume() == pytest.approx(0, abs=1e-3)  # seats without collision
    assert (lipped ^ upper.translate((1.0, 0, 0))).volume() > 50  # but cannot slide sideways
    assert (lipped ^ upper.translate((0, 0, -0.3))).volume() > 100  # and rests on the bin top


def test_lip_keeps_pockets_inside_the_rim():
    edge_pocket = [{"points": [[0, 10], [40, 10], [40, 30], [0, 30]], "depth": 8}]
    lipped, info = build(pockets=edge_pocket, lip=True)
    plain, _ = build(pockets=edge_pocket)
    assert info["clipped"]
    rim = (lipped - plain).volume()
    assert rim > 1500  # the full rim survives (about 2.2 cm3 for a 2x3 bin)
