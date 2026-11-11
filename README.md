# Tooltracer

Photograph tools on a sheet of paper, get a 3D-printable Gridfinity bin with tool-shaped pockets.
Everything runs on your machine; nothing is uploaded anywhere.

## Run

```bash
uv sync --extra ai
```

```bash
uv run tooltracer
```

Opens http://127.0.0.1:8765. (Skip `--extra ai` if you only want classic detection.)

## Workflow

1. **Photo** – lay tools flat on a sheet, shoot from straight above and as high as is practical
   (tall tools lean outward in a close-up shot and trace oversized). Drop the photo in.
2. **Check the trace** – the *Flattened + traces* view shows the outlines on the de-skewed sheet.
   If the paper was found wrong, switch to *Original*, drag the four corner handles, and re-trace.
3. **Layout** – drag tools, `R` rotates 90°, arrows nudge, `Del` removes. Add finger holes. Set grid size,
   pocket depth (per tool or default), and clearance.
4. **3D preview → Download STL.**

## Scale reference

- **Plain paper** – Letter, A4, Legal, Tabloid, A3 or a custom size. The sheet's corners set scale and perspective,
  so the whole sheet must be visible against a darker surface.
- **Marker sheet** – print the sheet linked in the sidebar at 100 % and verify its 100 mm ruler.
  More robust than corner-finding (works on a light table, tolerates tools overhanging the edge).

## Detection

- **Classic** – OpenCV: anything that differs from blank paper. Fast and accurate with even light;
  hard shadows get traced as part of the tool (lower the sensitivity or soften the light).
- **AI** – IS-Net salient-object segmentation via ONNX Runtime (CPU). The first use downloads a ~170 MB model
  into `models/`; afterwards it is offline. Better with shadows, can miss small or flat parts.

## Bin

Standard 42 mm Gridfinity footprint with the stepped base profile, 7 mm height units (auto-raised to keep at
least 1 mm of floor under the deepest pocket), optional 6.5 × 2.4 mm magnet holes. No stacking lip.

## Test without a camera

```bash
uv run python scripts/make_test_photo.py out/test.jpg
```

Generates a synthetic photo with known-size tools (add `--markers` for the marker-sheet variant).

## Layout of the code

- `tooltracer/rectify.py` – paper / ArUco detection and perspective correction (5 px per mm)
- `tooltracer/segment.py` – classic and AI masks → smoothed outlines in mm
- `tooltracer/gridfinity.py` – bin solid (manifold3d) and STL export
- `tooltracer/server.py` – FastAPI app; `tooltracer/static/` – the UI (three.js vendored, MIT)
