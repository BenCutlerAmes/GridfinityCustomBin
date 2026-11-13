import importlib.util
from pathlib import Path

import cv2
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_photo_module():
    spec = importlib.util.spec_from_file_location("make_test_photo", ROOT / "scripts" / "make_test_photo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def photo_paper():
    """Synthetic photo: hex key (40 x 100 mm), screwdriver (24 x 150 mm), washer (30 mm) on Letter paper."""
    return _load_photo_module().make(False)


@pytest.fixture(scope="session")
def photo_markers():
    return _load_photo_module().make(True)


@pytest.fixture(scope="session")
def photo_jpeg(photo_paper):
    ok, buf = cv2.imencode(".jpg", photo_paper, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    return buf.tobytes()


# Known sizes of the tools in the synthetic photo, as (short side, long side) in mm.
EXPECTED_TOOLS = [(24.0, 150.0), (40.0, 100.0), (30.0, 30.0)]


def assert_tools_match(tools, expected=EXPECTED_TOOLS, tol=1.5):
    found = sorted(tuple(sorted((t["width"], t["height"]))) for t in tools)
    want = sorted(tuple(sorted(e)) for e in expected)
    assert len(found) == len(want), f"expected {len(want)} tools, found {found}"
    for f, w in zip(found, want):
        assert abs(f[0] - w[0]) <= tol and abs(f[1] - w[1]) <= tol, f"size {f} not within {tol} mm of {w}"
