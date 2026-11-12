#!/usr/bin/env bash
# Build a .deb from the PyInstaller output in dist/Tooltracer/.
#   packaging/linux/build_deb.sh 1.2.3
# Installing a newer .deb over an older one is a normal package upgrade (same package name).
set -euo pipefail

VERSION="${1:?usage: build_deb.sh <version>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$ROOT/dist/Tooltracer"
PKG="$ROOT/dist/deb"
OUT="$ROOT/dist/tooltracer_${VERSION}_amd64.deb"

[ -x "$SRC/Tooltracer" ] || { echo "Missing $SRC/Tooltracer - run PyInstaller first." >&2; exit 1; }

rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications"
cp -a "$SRC" "$PKG/opt/tooltracer"
ln -s /opt/tooltracer/Tooltracer "$PKG/usr/bin/tooltracer"

cat > "$PKG/usr/share/applications/tooltracer.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tooltracer
Comment=Trace tools from photos and build Gridfinity bins
Exec=/opt/tooltracer/Tooltracer
Terminal=false
Categories=Graphics;Engineering;
EOF

cat > "$PKG/DEBIAN/control" <<EOF
Package: tooltracer
Version: ${VERSION}
Section: graphics
Priority: optional
Architecture: amd64
Maintainer: Tooltracer <noreply@example.invalid>
Installed-Size: $(du -sk "$PKG/opt" | cut -f1)
Depends: libc6, libegl1, libgl1, libnss3, libxkbcommon-x11-0, libxcb-cursor0, libasound2 | libasound2t64
Description: Trace tools from photos and build Gridfinity bins
 Photograph tools on a sheet of paper, keep a library of their outlines,
 and generate 3D-printable Gridfinity bins with tool-shaped pockets.
EOF

dpkg-deb --build --root-owner-group "$PKG" "$OUT"
rm -rf "$PKG"
echo "Built $OUT"
