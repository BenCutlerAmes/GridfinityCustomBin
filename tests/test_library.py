"""The on-disk library: what users accumulate over time must survive restarts and upgrades."""
import json

import numpy as np
import pytest

from gridfinity_custom_bin.library import FORMAT, Library, LibraryError, load_config, save_config

OUTLINE = [[50, 40], [90, 40], [90, 140], [50, 140]]


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "lib")


@pytest.fixture
def sheet():
    return np.full((1397, 1080, 3), 240, np.uint8)  # a blank flattened Letter sheet


def test_tool_round_trip_survives_reopening(lib, sheet):
    tool = lib.add_tool(OUTLINE, sheet, "  Pliers ", "Hand, hand , GRIP", 12)
    assert tool["name"] == "Pliers" and tool["tags"] == ["hand", "grip"] and tool["thickness"] == 12.0
    assert (tool["width"], tool["height"]) == (40.0, 100.0)
    xs = [p[0] for p in tool["points"]]
    assert min(xs) == -20 and max(xs) == 20  # stored centred on the tool
    assert lib.tool_photo(tool["id"]).stat().st_size > 0

    again = Library(lib.root).list_tools()
    assert [t["id"] for t in again] == [tool["id"]] and again[0]["points"] == tool["points"]


def test_update_and_delete_tool(lib, sheet):
    tool = lib.add_tool(OUTLINE, sheet, "", [], None)
    assert tool["name"] == "Untitled tool" and tool["thickness"] is None
    updated = lib.update_tool(tool["id"], {"name": "Wrench", "tags": "metric", "thickness": 6})
    assert (updated["name"], updated["tags"], updated["thickness"]) == ("Wrench", ["metric"], 6.0)
    assert lib.update_tool(tool["id"], {"thickness": None})["thickness"] is None
    lib.delete_tool(tool["id"])
    assert lib.list_tools() == []
    with pytest.raises(LibraryError):
        lib.get_tool(tool["id"])


def test_bins_save_update_and_delete(lib):
    rec = lib.save_bin({"name": "Drawer 1", "settings": {"nx": 3}, "items": [{"kind": "tool", "tool_id": "a" * 12, "x": 1, "y": 2}]})
    same = lib.save_bin({"name": "Drawer 1b", "settings": {"nx": 4}, "items": []}, rec["id"])
    assert same["id"] == rec["id"] and same["created"] == rec["created"] and same["settings"] == {"nx": 4}
    assert [b["name"] for b in lib.list_bins()] == ["Drawer 1b"]
    lib.delete_bin(rec["id"])
    assert lib.list_bins() == []


@pytest.mark.parametrize("bad", ["../../etc", "", "ABCDEFABCDEF", "abc", "a" * 13])
def test_ids_cannot_escape_the_library(lib, bad):
    for call in (lib.get_tool, lib.delete_tool, lib.tool_photo, lib.delete_bin):
        with pytest.raises(LibraryError):
            call(bad)


def test_half_written_entries_do_not_break_listing(lib, sheet):
    good = lib.add_tool(OUTLINE, sheet, "Good", [], None)
    (lib.root / "tools" / ("b" * 12)).mkdir()  # folder with no tool.json
    (lib.root / "bins" / ("c" * 12 + ".json")).write_text("{not json")
    assert [t["id"] for t in lib.list_tools()] == [good["id"]]
    assert lib.list_bins() == []


def test_library_from_a_newer_version_is_refused(lib):
    marker = lib.root / "library.json"
    data = json.loads(marker.read_text())
    marker.write_text(json.dumps({**data, "format": FORMAT + 1}))
    with pytest.raises(LibraryError, match="newer version"):
        Library(lib.root)
    marker.write_text(json.dumps({**data, "format": FORMAT}))
    Library(lib.root)  # same format opens fine, as after an upgrade


def test_config_round_trip(tmp_path):
    assert load_config(tmp_path / "missing") == {}
    save_config(tmp_path / "cfg", {"library_path": "x"})
    assert load_config(tmp_path / "cfg") == {"library_path": "x"}
