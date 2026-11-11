"""Persistent tool library: plain files in a folder the user chooses.

    <library>/
        library.json              format marker
        tools/<id>/tool.json      name, tags, thickness, outline (mm, centred on the tool)
        tools/<id>/photo.jpg      crop of the flattened photo around the tool
        bins/<id>.json            a saved bin: settings + which tools sit where

A small config file in the per-user config directory remembers where the library is.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import platformdirs

from .rectify import PX_PER_MM

FORMAT = 1
PHOTO_MARGIN_MM = 4.0
_ID = re.compile(r"^[0-9a-f]{12}$")


class LibraryError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _check_id(item_id: str) -> str:
    if not _ID.match(item_id or ""):
        raise LibraryError("Invalid id.")
    return item_id


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def clean_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = tags.split(",")
    out = []
    for t in tags or []:
        t = str(t).strip().lower()[:40]
        if t and t not in out:
            out.append(t)
    return out


# ---------- config ----------


def config_dir(override: str | None = None) -> Path:
    # Absolute: the embedded webview refuses a relative storage folder.
    return Path(override).resolve() if override else Path(platformdirs.user_config_dir("Tooltracer", False))


def load_config(cfg_dir: Path) -> dict:
    try:
        return _read_json(cfg_dir / "config.json")
    except Exception:
        return {}


def save_config(cfg_dir: Path, cfg: dict) -> None:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    _write_json(cfg_dir / "config.json", cfg)


def default_library_path() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "Tooltracer Library"


# ---------- library ----------


class Library:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()
        if not self.root.is_absolute():
            self.root = self.root.resolve()
        try:
            (self.root / "tools").mkdir(parents=True, exist_ok=True)
            (self.root / "bins").mkdir(parents=True, exist_ok=True)
            marker = self.root / "library.json"
            if not marker.exists():
                _write_json(marker, {"app": "tooltracer", "format": FORMAT, "created": _now()})
        except OSError as e:
            raise LibraryError(f"Cannot use that folder: {e.strerror or e}") from e

    # tools

    def _tool_dir(self, tool_id: str) -> Path:
        return self.root / "tools" / _check_id(tool_id)

    def list_tools(self) -> list[dict]:
        tools = []
        for d in (self.root / "tools").iterdir():
            try:
                if d.is_dir() and _ID.match(d.name):
                    tools.append(_read_json(d / "tool.json"))
            except Exception:
                continue  # skip half-written or foreign folders rather than failing the whole list
        tools.sort(key=lambda t: t.get("name", "").lower())
        return tools

    def get_tool(self, tool_id: str) -> dict:
        try:
            return _read_json(self._tool_dir(tool_id) / "tool.json")
        except FileNotFoundError:
            raise LibraryError("Tool not found.")

    def add_tool(self, sheet_points, rect_img: np.ndarray, name: str, tags, thickness, source: dict | None = None) -> dict:
        """sheet_points: outline in flattened-sheet mm. rect_img: the flattened photo (PX_PER_MM)."""
        pts = np.asarray(sheet_points, dtype=float)
        if pts.ndim != 2 or len(pts) < 3:
            raise LibraryError("Outline needs at least 3 points.")
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        centre = (lo + hi) / 2
        h_px, w_px = rect_img.shape[:2]
        x0 = max(0, int(np.floor((lo[0] - PHOTO_MARGIN_MM) * PX_PER_MM)))
        y0 = max(0, int(np.floor((lo[1] - PHOTO_MARGIN_MM) * PX_PER_MM)))
        x1 = min(w_px, int(np.ceil((hi[0] + PHOTO_MARGIN_MM) * PX_PER_MM)))
        y1 = min(h_px, int(np.ceil((hi[1] + PHOTO_MARGIN_MM) * PX_PER_MM)))
        crop = rect_img[y0:y1, x0:x1]

        tool_id = _new_id()
        d = self._tool_dir(tool_id)
        d.mkdir(parents=True)
        cv2.imwrite(str(d / "photo.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        now = _now()
        tool = {
            "id": tool_id,
            "name": (name or "").strip()[:80] or "Untitled tool",
            "tags": clean_tags(tags),
            "thickness": float(thickness) if thickness else None,
            "points": np.round(pts - centre, 3).tolist(),  # mm, y down, origin at the bounding-box centre
            "width": round(float(hi[0] - lo[0]), 1),
            "height": round(float(hi[1] - lo[1]), 1),
            # Where photo.jpg sits in the same coordinates as `points`.
            "photo": {
                "x": round(x0 / PX_PER_MM - centre[0], 3),
                "y": round(y0 / PX_PER_MM - centre[1], 3),
                "w": round((x1 - x0) / PX_PER_MM, 3),
                "h": round((y1 - y0) / PX_PER_MM, 3),
            },
            "source": source or {},
            "created": now,
            "updated": now,
        }
        _write_json(d / "tool.json", tool)
        return tool

    def update_tool(self, tool_id: str, patch: dict) -> dict:
        tool = self.get_tool(tool_id)
        if "name" in patch:
            tool["name"] = str(patch["name"] or "").strip()[:80] or "Untitled tool"
        if "tags" in patch:
            tool["tags"] = clean_tags(patch["tags"])
        if "thickness" in patch:
            t = patch["thickness"]
            tool["thickness"] = float(t) if t else None
        tool["updated"] = _now()
        _write_json(self._tool_dir(tool_id) / "tool.json", tool)
        return tool

    def delete_tool(self, tool_id: str) -> None:
        d = self._tool_dir(tool_id)
        if d.is_dir():
            shutil.rmtree(d)

    def tool_photo(self, tool_id: str) -> Path:
        p = self._tool_dir(tool_id) / "photo.jpg"
        if not p.exists():
            raise LibraryError("Photo not found.")
        return p

    # bins

    def _bin_path(self, bin_id: str) -> Path:
        return self.root / "bins" / f"{_check_id(bin_id)}.json"

    def list_bins(self) -> list[dict]:
        bins = []
        for p in (self.root / "bins").glob("*.json"):
            try:
                if _ID.match(p.stem):
                    bins.append(_read_json(p))
            except Exception:
                continue
        bins.sort(key=lambda b: b.get("updated", ""), reverse=True)
        return bins

    def save_bin(self, data: dict, bin_id: str | None = None) -> dict:
        now = _now()
        if bin_id:
            path = self._bin_path(bin_id)
            created = _read_json(path).get("created", now) if path.exists() else now
        else:
            bin_id, created = _new_id(), now
            path = self._bin_path(bin_id)
        record = {
            "id": bin_id,
            "name": str(data.get("name") or "").strip()[:80] or "Untitled bin",
            "settings": data.get("settings") or {},
            "items": data.get("items") or [],
            "created": created,
            "updated": now,
        }
        _write_json(path, record)
        return record

    def delete_bin(self, bin_id: str) -> None:
        self._bin_path(bin_id).unlink(missing_ok=True)

    def summary(self) -> dict:
        return {
            "path": str(self.root),
            "name": self.root.name,
            "tools": sum(1 for d in (self.root / "tools").iterdir() if d.is_dir()),
            "bins": sum(1 for _ in (self.root / "bins").glob("*.json")),
        }
