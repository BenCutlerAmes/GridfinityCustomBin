# Tooltracer

Photograph tools on a sheet of paper, build up a library of their outlines, and generate 3D-printable
Gridfinity bins with tool-shaped pockets. Everything runs on your machine; nothing is uploaded anywhere.

## Run

```bash
uv sync --extra ai
```

```bash
uv run tooltracer
```

Opens a desktop window (Windows, macOS, Linux). Skip `--extra ai` if you only want classic detection.

- `uv run tooltracer --browser` – use your browser instead of a window (also the automatic fallback if no
  window backend is available).
- `--library <folder>` – use a library folder for this run only; `--config-dir <folder>` – keep settings elsewhere.
- Linux: the window uses Qt (installed automatically). macOS and Linux are untested so far.

## Modes

- **Quick bin** – one photo straight to a bin. Nothing is saved.
- **Capture tools** – trace a photo, then name each outline, tag it, note its thickness and save it to your library.
  The photo is shared with Quick bin, so you can trace once and do both.
- **Library** – browse, search, filter by tag, rename, re-tag, set thickness, delete.
- **Build bin** – combine library tools (from any number of photos) into a bin. Bins are saved automatically and can
  be reopened, duplicated and re-exported. If a tool is edited later, bins that use it pick up the change.

### Pocket depth in Build bin

By default every pocket is cut to the same depth. Untick **Same depth** and each pocket uses its tool's saved
thickness (or the default depth if it has none); select a pocket to override it individually.

## Your library folder

On first use of Capture/Library/Build you choose a folder; change it any time from the top-right button.
It holds plain files, so it can live in Dropbox/OneDrive/iCloud and be backed up or copied between machines:

```
<library>/
  library.json
  tools/<id>/tool.json    name, tags, thickness, outline in mm
  tools/<id>/photo.jpg    flattened photo crop of the tool
  bins/<id>.json          bin settings + which tools sit where
```

Only the folder's location is stored outside it (in the per-user config directory).

## Taking the photo

Lay tools flat on a sheet, shoot from straight above and as high as is practical (tall tools lean outward in a
close-up shot and trace oversized). After tracing, *Flattened + traces* shows the outlines on the de-skewed sheet.
If the paper was found wrong, switch to *Original*, drag the four corner handles, and re-trace.

**Scale reference**

- **Plain paper** – Letter, A4, Legal, Tabloid, A3 or a custom size. The sheet's corners set scale and perspective,
  so the whole sheet must be visible against a darker surface.
- **Marker sheet** – print the sheet linked in the sidebar at 100 % and verify its 100 mm ruler.
  More robust than corner-finding (works on a light table, tolerates tools overhanging the edge).

**Detection**

- **Classic** – OpenCV: anything that differs from blank paper. Fast and accurate with even light;
  hard shadows get traced as part of the tool (lower the sensitivity or soften the light).
- **AI** – IS-Net salient-object segmentation via ONNX Runtime (CPU). The first use downloads a ~170 MB model
  into `models/`; afterwards it is offline. Better with shadows, can miss small or flat parts.

## Layout and bin

Drag tools, `R` rotates 90°, arrows nudge, `Del` removes. Add finger holes. Set grid size, depth and clearance,
then check the 3D preview and save the STL.

Standard 42 mm Gridfinity footprint with the stepped base profile, 7 mm height units (auto-raised to keep at
least 1 mm of floor under the deepest pocket), optional 6.5 × 2.4 mm magnet holes.

Optional **stacking lip**: a 3.9 mm rim around the top whose inner face mirrors the foot profile, so another
Gridfinity bin locks on top. The rim is 2.6 mm thick at its base, so pockets are kept that far from the edge
(the layout view shows the rim), and tools need to sit flush with or below the bin top for a bin to stack on them.

## Test without a camera

```bash
uv run python scripts/make_test_photo.py out/test.jpg
```

Generates a synthetic photo with known-size tools (add `--markers` for the marker-sheet variant).

## Layout of the code

- `tooltracer/launcher.py` – starts the local server and the desktop window (pywebview) or browser
- `tooltracer/server.py` – FastAPI app: tracing, library, saved bins, STL
- `tooltracer/library.py` – the on-disk library and the settings file
- `tooltracer/rectify.py` – paper / ArUco detection and perspective correction (5 px per mm)
- `tooltracer/segment.py` – classic and AI masks → smoothed outlines in mm
- `tooltracer/gridfinity.py` – bin solid (manifold3d) and STL export
- `tooltracer/static/` – the UI (three.js vendored, MIT)
