# Tooltracer

Photograph tools on a sheet of paper, build up a library of their outlines, and generate 3D-printable
Gridfinity bins with tool-shaped pockets. Everything runs on your machine; nothing is uploaded anywhere.

## Install

Download the latest build for your system from the repository's **Releases** page. Every past version stays
listed there.

| System | File | Install / upgrade |
| --- | --- | --- |
| Windows | `Tooltracer-<version>-windows-setup.exe` | Run it. Running a newer one upgrades the installed copy in place. |
| Windows (no install) | `Tooltracer-<version>-windows-portable.zip` | Unzip anywhere and run `Tooltracer.exe`. |
| macOS (Apple Silicon) | `Tooltracer-<version>-macos-arm64.dmg` | Drag Tooltracer to Applications; choose *Replace* to upgrade. |
| Debian / Ubuntu | `tooltracer_<version>_amd64.deb` | `sudo apt install ./tooltracer_<version>_amd64.deb` (also upgrades). |
| Other Linux | `Tooltracer-<version>-linux-x86_64.tar.gz` | Unpack and run `Tooltracer/Tooltracer`. |

Upgrading never touches your data: the tool library is in the folder you chose and settings are in your user
profile, both outside the program folder. The builds are not code-signed, so Windows SmartScreen
("More info → Run anyway") and macOS Gatekeeper (right-click the app → Open) warn on first launch.

## Releasing a new version

Releases are built by GitHub Actions for Windows, macOS and Linux, on demand:

1. On GitHub open **Actions → Release builds → Run workflow**.
2. Enter the version (e.g. `0.3.0`) and run it. Pushing a tag does the same: `git tag v0.3.0 && git push origin v0.3.0`.

The workflow builds and tests each app (see below) and only if all three pass publishes a GitHub Release
`v<version>` with all the files. A version that already exists is refused rather than overwritten.

## Automated tests

Every pull request runs **Build check** (again on each new commit pushed to it), which does everything a
release does except publish, so a branch that would break a build is caught before it is merged. On Windows, macOS and Linux it:

1. runs the source tests (`tests/`): photo → outline accuracy, bin geometry, the library, the HTTP API;
2. builds the app;
3. tests the built app from the outside (`packaging/test_packaged.py`): uploads a synthetic photo with
   known tool sizes, traces it (classic and AI), saves tools and a bin to a library, exports an STL, then opens
   the real desktop window and checks the UI loaded in it;
4. builds the installer, installs an older version, upgrades it with the new one, checks the upgrade replaced
   it cleanly, and re-tests the installed copy.

A build check keeps no installers: they are built, tested and discarded, and only a release stores them (on
the release itself). Logs, results and a screenshot of the window are kept for 3 days under the run's
*Artifacts* as `test-output-<os>`. macOS runners are billed at a higher rate on private repositories, so each
run uses a noticeable share of the free Actions minutes.

Run the same tests locally:

```bash
uv run --group dev pytest
```

```bash
uv run --group dev python packaging/test_packaged.py --exe dist/Tooltracer/Tooltracer.exe --ai --window
```

To build the app for your own system only:

```bash
uv run --group build pyinstaller packaging/tooltracer.spec --noconfirm
```

The result is in `dist/`. The Windows installer is then made with Inno Setup
(`iscc /DAppVersion=0.3.0 packaging\windows\tooltracer.iss`), the `.deb` with `packaging/linux/build_deb.sh 0.3.0`.

## Run from source

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
- `packaging/` – PyInstaller spec and the Windows / Linux installer scripts
- `tests/` – source tests; `packaging/test_packaged.py` – tests for a built or installed app
- `.github/workflows/` – `build.yml` (shared build + tests), `ci.yml` (branch build check), `release.yml` (manual release)
