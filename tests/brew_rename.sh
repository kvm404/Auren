#!/usr/bin/env bash
# The rename of the Homebrew formula from auren (0.1.8 and older) to auren-macos, on real Homebrew, the
# way the tap does it on the first release after it (.github/workflows/brew.yml): a 0.1.8 install of
# auren, then the tap renames it with formula_renames.json, then 0.1.8's auren update runs its two brew
# commands. Checks what docs/macos.md ("From 0.1.8 or older") promises:
#   - trust doesn't follow the rename: brew upgrade auren fails until auren-macos is trusted
#   - after brew trust --formula .../auren-macos, 0.1.8's brew upgrade --formula auren upgrades it
#   - opt/auren (what 0.1.8's updater reads) and the auren wrapper point at the new auren-macos keg
# macOS CI only: it installs into this machine's Homebrew. Not set up (no auren setup).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
work="${RUNNER_TEMP:-$(mktemp -d)}/brew-rename"
tap=local/auren-rename
rm -rf "$work"
mkdir -p "$work"
prefix="$(brew --prefix)"
brew trust --help >/dev/null || { echo "this Homebrew has no brew trust (6.0.0 or newer has)"; exit 1; }

# This commit as two versions: only the tarball's name (and so the formula's version) differs
for v in 0.1.8 0.1.9; do
  git -C "$root" archive --format=tar.gz --prefix="Auren-$v/" -o "$work/Auren-$v.tar.gz" HEAD
done
formula() {   # formula VERSION CLASS: packaging/homebrew/auren-macos.rb for that tarball, as that class
  local sum
  sum=$(shasum -a 256 "$work/Auren-$1.tar.gz" | cut -d' ' -f1)
  sed -e "s#^  url \".*\"\$#  url \"file://$work/Auren-$1.tar.gz\"#" -e "s#^  sha256 \".*\"\$#  sha256 \"$sum\"#" \
      -e "s#^class AurenMacos < Formula\$#class $2 < Formula#" "$root/packaging/homebrew/auren-macos.rb"
}
git_() { git -C "$work/src" -c user.name=ci -c user.email=ci@example.invalid "$@"; }

# The tap as it is at 0.1.8: Formula/auren.rb, in a git repository brew update fetches from
git init -q --bare --initial-branch=main "$work/tap.git"
git init -q --initial-branch=main "$work/src"
mkdir "$work/src/Formula"
formula 0.1.8 Auren >"$work/src/Formula/auren.rb"
git_ add -A && git_ commit -qm "auren 0.1.8" && git_ push -q "$work/tap.git" main
brew tap "$tap" "file://$work/tap.git"
brew install --formula "$tap/auren"                       # as the README said until 0.1.8: trusts auren only
[[ $(realpath "$prefix/opt/auren") == "$(realpath "$prefix/Cellar/auren/0.1.8")" ]]

# The first release after the rename, as brew.yml publishes it
git_ rm -q Formula/auren.rb
formula 0.1.9 AurenMacos >"$work/src/Formula/auren-macos.rb"
echo '{"auren": "auren-macos"}' >"$work/src/formula_renames.json"
git_ add -A && git_ commit -qm "auren-macos 0.1.9" && git_ push -q "$work/tap.git" main

# 0.1.8's auren update, before auren-macos is trusted: brew update, then brew upgrade --formula auren
export HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_ENV_HINTS=1
unset HOMEBREW_NO_AUTO_UPDATE
brew update --quiet || echo "brew update failed (0.1.8's updater goes on)"
if out=$(brew upgrade --formula auren 2>&1); then
  echo "$out"
  echo "brew upgrade auren worked without trusting auren-macos: Homebrew changed, update docs/macos.md"
  exit 1
fi
echo "$out"
grep -q "auren-macos" <<<"$out" || { echo "the refusal doesn't name auren-macos"; exit 1; }

# What docs/macos.md says to run once, then 0.1.8's auren update again
brew trust --formula "$tap/auren-macos"
brew update --quiet || true
brew upgrade --formula auren
brew list --versions auren-macos | grep -qw "0\.1\.9"        # and 0.1.8, kept: no cleanup
keg="$(realpath "$prefix/Cellar/auren-macos/0.1.9")"
[[ $(realpath "$prefix/opt/auren-macos") == "$keg" ]]
[[ $(realpath "$prefix/opt/auren") == "$keg" ]] || { echo "opt/auren isn't the new keg: 0.1.8's updater won't find it"; exit 1; }
test -f "$prefix/opt/auren/libexec/source/install-macos.sh"
grep -qF "$prefix/opt/auren-macos/libexec/source" "$prefix/bin/auren"
brew upgrade --formula auren-macos                         # the next version's updater: nothing to do

# A fresh install by the new name trusts itself, as the README says
brew uninstall --formula auren-macos
brew untrust --formula "$tap/auren-macos"
brew install --formula "$tap/auren-macos"
[[ $(realpath "$prefix/opt/auren-macos") == "$(realpath "$prefix/Cellar/auren-macos/0.1.9")" ]]
brew upgrade --formula auren-macos
brew uninstall --formula auren-macos
brew untap "$tap"
echo "the rename works"
