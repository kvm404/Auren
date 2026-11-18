#!/usr/bin/env bash
# Build the Arch package from this checkout (committed files only, at HEAD), the way the AUR builds it
# from a release: the same PKGBUILD, with the source tarball made here instead of downloaded.
#
#   packaging/arch/build-local.sh [OUT_DIR] [makepkg args…]
#
# OUT_DIR (default: a new temporary folder) gets the package; install it with sudo pacman -U.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(git -C "$here" rev-parse --show-toplevel)"
out="${1:-$(mktemp -d)}"
shift || true
mkdir -p "$out"
out="$(cd "$out" && pwd)"

pkgver="$(sed -n 's/^pkgver=//p' "$here/PKGBUILD")"
cp "$here/PKGBUILD" "$here/auren.install" "$out/"
git -C "$repo" archive --format=tar.gz --prefix="Auren-$pkgver/" -o "$out/auren-$pkgver.tar.gz" HEAD
cd "$out"
makepkg --force --cleanbuild --skipchecksums "$@"
echo "built in $out:"
ls "$out"/*.pkg.tar.*
