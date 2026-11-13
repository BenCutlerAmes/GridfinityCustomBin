"""The whole flow through the HTTP API, the way the UI drives it."""
import json

import pytest
from fastapi.testclient import TestClient

from gridfinity_custom_bin import __version__, server

from .conftest import assert_tools_match


@pytest.fixture
def client(tmp_path):
    server.init_state(str(tmp_path / "cfg"), None)
    server._sessions.clear()
    with TestClient(server.app) as c:
        yield c
    server.init_state(str(tmp_path / "cfg-unused"), None)


def trace(client, photo_jpeg, **opts):
    up = client.post("/api/upload", files={"file": ("photo.jpg", photo_jpeg, "image/jpeg")})
    assert up.status_code == 200
    sid = up.json()["id"]
    res = client.post("/api/trace", json={"id": sid, **opts})
    assert res.status_code == 200, res.text
    return sid, res.json()


def test_config_and_ui_are_served(client):
    cfg = client.get("/api/config").json()
    assert cfg["version"] == __version__ and cfg["library"] is None and cfg["native"] is False
    assert "Gridfinity Custom Bin" in client.get("/").text
    for path in ("/static/app.js", "/static/style.css", "/static/vendor/three.module.min.js"):
        assert client.get(path).status_code == 200
    assert client.get("/markers?paper=a4").status_code == 200
    assert client.get("/api/marker/0.png").headers["content-type"] == "image/png"


def test_diagnostics_lists_native_libraries(client):
    d = client.get("/api/diagnostics").json()
    assert d["version"] == __version__
    for lib in ("opencv", "numpy", "shapely", "manifold3d"):
        assert d["libraries"][lib], lib


def test_quick_flow_photo_to_stl(client, photo_jpeg):
    sid, data = trace(client, photo_jpeg)
    assert data["sheet_mm"] == [215.9, 279.4] and data["warning"] is None
    assert_tools_match(data["tools"])
    assert client.get(f"/api/rectified/{sid}.jpg").status_code == 200

    pockets = [{"points": [[x - 30, y - 40] for x, y in t["points"]], "depth": 8} for t in data["tools"]]
    res = client.post("/api/bin", json={"nx": 4, "ny": 4, "pockets": pockets, "lip": True, "magnets": True, "name": "My bin!"})
    assert res.status_code == 200
    info = json.loads(res.headers["x-bin-info"])
    assert info["lip"] and not info["clipped"] and len(res.content) == 84 + 50 * info["triangles"]
    assert 'filename="gridfinity_4x4x2_My_bin_lip.stl"' in res.headers["content-disposition"]


def test_library_is_required_then_persists(client, photo_jpeg, tmp_path):
    assert client.get("/api/tools").status_code == 409  # no library chosen yet
    lib_dir = tmp_path / "My Library"
    assert client.post("/api/library", json={"path": str(lib_dir)}).json()["tools"] == 0

    sid, data = trace(client, photo_jpeg)
    new = [{"points": t["points"], "name": f"Tool {i}", "tags": ["test"], "thickness": 5 + i} for i, t in enumerate(data["tools"])]
    saved = client.post("/api/tools", json={"session_id": sid, "tools": new}).json()
    assert len(saved) == 3 and client.get(f"/api/tools/{saved[0]['id']}/photo.jpg").status_code == 200

    patched = client.patch(f"/api/tools/{saved[0]['id']}", json={"name": "Renamed"}).json()
    assert patched["name"] == "Renamed" and patched["thickness"] == 5.0  # untouched fields are kept

    items = [{"kind": "tool", "tool_id": t["id"], "x": 50, "y": 50, "rot": 0, "depth": None} for t in saved]
    rec = client.post("/api/bins", json={"name": "Drawer", "settings": {"nx": 4, "ny": 4, "uniform": False}, "items": items}).json()
    client.put(f"/api/bins/{rec['id']}", json={"name": "Drawer 2", "settings": {"nx": 5}, "items": items[:2]})

    # A fresh start (new process, or the next version after an upgrade) finds everything again.
    server.init_state(str(tmp_path / "cfg"), None)
    assert client.get("/api/config").json()["library"]["tools"] == 3
    assert {t["name"] for t in client.get("/api/tools").json()} == {"Renamed", "Tool 1", "Tool 2"}
    bins = client.get("/api/bins").json()
    assert [(b["name"], len(b["items"])) for b in bins] == [("Drawer 2", 2)]

    assert client.delete(f"/api/tools/{saved[2]['id']}").status_code == 200
    assert client.delete(f"/api/bins/{rec['id']}").status_code == 200
    assert len(client.get("/api/tools").json()) == 2 and client.get("/api/bins").json() == []


def test_bad_requests_are_rejected_cleanly(client, photo_jpeg):
    assert client.post("/api/upload", files={"file": ("x.jpg", b"not an image", "image/jpeg")}).status_code == 400
    assert client.post("/api/trace", json={"id": "nope"}).status_code == 404
    sid, _ = trace(client, photo_jpeg)
    assert client.post("/api/trace", json={"id": sid, "reference": "markers"}).status_code == 422
    assert client.post("/api/trace", json={"id": sid, "paper": "custom", "custom_size": [5, 5]}).status_code == 400
    assert client.post("/api/bin", json={"nx": 0, "ny": 1}).status_code == 422
    assert client.post("/api/library/browse").status_code == 400  # desktop window only
