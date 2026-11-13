"""Photo -> flattened sheet -> tool outlines, checked against known tool sizes."""
import numpy as np
import pytest

from gridfinity_custom_bin import rectify, segment

from .conftest import assert_tools_match

LETTER = rectify.PAPER_SIZES_MM["letter"]


def test_paper_reference_gives_true_scale(photo_paper):
    corners = rectify.detect_paper(photo_paper)
    # The synthetic sheet's corners are placed at known pixels.
    expected = np.array([[420, 380], [2010, 300], [2120, 2650], [300, 2560]], dtype=float)
    assert np.abs(corners - expected).max() < 15
    rect, w, h = rectify.rectify_paper(photo_paper, corners, LETTER)
    assert (w, h) == (215.9, 279.4)  # portrait, not swapped
    assert rect.shape[:2] == (round(h * rectify.PX_PER_MM), round(w * rectify.PX_PER_MM))
    assert_tools_match(segment.find_tools(rect, "classic"))


def test_marker_reference_gives_true_scale(photo_markers):
    rect, w, h, ignore, page = rectify.rectify_markers(photo_markers, LETTER)
    assert (w, h) == (215.9, 279.4)
    # The markers themselves must not be reported as tools.
    assert_tools_match(segment.find_tools(rect, "classic", ignore=ignore))


def test_markers_missing_is_a_clear_error(photo_paper):
    with pytest.raises(rectify.RectifyError, match="markers"):
        rectify.rectify_markers(photo_paper, LETTER)


def test_blank_image_reports_no_paper():
    with pytest.raises(rectify.RectifyError):
        rectify.detect_paper(np.full((600, 800, 3), 40, np.uint8))


def test_outline_is_not_mirrored(photo_paper):
    """The hex key's short leg points right at the bottom; a mirrored trace would put it on the left."""
    rect, _, _ = rectify.rectify_paper(photo_paper, rectify.detect_paper(photo_paper), LETTER)
    tools = segment.find_tools(rect, "classic")
    key = next(t for t in tools if 95 < t["height"] < 105)
    pts = np.array(key["points"])
    bottom = pts[pts[:, 1] > pts[:, 1].max() - 4]  # points along the foot of the L
    top = pts[pts[:, 1] < pts[:, 1].min() + 4]
    assert bottom[:, 0].max() - top[:, 0].max() > 25  # foot extends ~32 mm further right than the stem


def test_min_area_filters_small_tools(photo_paper):
    rect, _, _ = rectify.rectify_paper(photo_paper, rectify.detect_paper(photo_paper), LETTER)
    assert len(segment.find_tools(rect, "classic", min_area_mm2=900)) == 2  # washer (~700 mm2) dropped
