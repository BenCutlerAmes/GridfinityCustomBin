# PyInstaller build for all three platforms.
#   uv run --group build pyinstaller packaging/gridfinity-custom-bin.spec --noconfirm
# Output: dist/GridfinityCustomBin/ (Windows, Linux) or dist/GridfinityCustomBin.app (macOS).
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "gridfinity_custom_bin" / "_version.py").read_text()).group(1)

datas = [(str(ROOT / "gridfinity_custom_bin" / "static"), "gridfinity_custom_bin/static")]
binaries = []
hiddenimports = collect_submodules("uvicorn")
for pkg in ("onnxruntime", "pillow_heif", "manifold3d", "shapely"):
    datas += collect_data_files(pkg)
    binaries += collect_dynamic_libs(pkg)

a = Analysis(
    [str(ROOT / "packaging" / "run_gridfinity_custom_bin.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)

if sys.platform == "win32":
    # Some dependency ships a very old msvcp140.dll (14.16) that PyInstaller puts at the top of
    # the bundle, where every extension finds it first. ONNX Runtime needs 14.40+ and crashes
    # with the old one. Ship the build machine's current VC++ runtime instead.
    import os

    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    runtime = ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"]
    present = [n for n in runtime if (system32 / n).exists()]
    a.binaries = [b for b in a.binaries if b[0].lower() not in present]
    a.binaries += [(n, str(system32 / n), "BINARY") for n in present]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GridfinityCustomBin",
    console=False,  # windowed app; output goes to gridfinity-custom-bin.log in the settings folder
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="GridfinityCustomBin", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="GridfinityCustomBin.app",
        bundle_identifier="com.gridfinitycustombin.app",
        version=VERSION.split("+")[0],
        info_plist={"NSHighResolutionCapable": True, "CFBundleDisplayName": "Gridfinity Custom Bin"},
    )
