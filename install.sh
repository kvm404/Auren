#!/usr/bin/env bash
# Install Auren for the current user on Omarchy. Safe to run again: it's also how you update.
#
#   ./install.sh            install into ~/.local/share/auren (a copy: the checkout can move or go)
#   ./install.sh --dev      the same, but linked to this checkout: code changes need only a restart
#   ./install.sh --check    only report what's missing (auren doctor, once installed)
#   --browser / --no-browser   set up the browser tools (Open Browser Use) or turn them off, without asking
#   --keys=super / --keys=alt / --no-keys   add the talk keys to bindings.lua, or only print them, without asking
#   --brain=auto|claude|codex  who answers; --yes  ask nothing
#
# Most people install the Arch package instead (yay -S auren): this is for working on Auren from a
# checkout. It installs the program, then runs `auren setup` for the per-user part (questions, talk
# keys, bar plugin, API key, voice model, services, browser tools), the same setup the package uses.
#
# Layout (see docs/architecture.md):
#   ~/.local/share/auren/venv/          the Python environment, with auren and auren-ctl
#   ~/.local/share/auren/mascot/        the floating mascot (Quickshell)
#   ~/.local/share/auren/bar-plugin/    the Omarchy bar plugin, linked as kvm404.auren
#   ~/.local/share/auren/bin/auren-ctl  what key bindings call (and bin/auren, what the bar plugin runs)
#   ~/.local/share/auren/models/        the voice model
#
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config="$(realpath -m "${XDG_CONFIG_HOME:-$HOME/.config}")"
data="$(realpath -m "${XDG_DATA_HOME:-$HOME/.local/share}")"
share="$data/auren"
units="$config/systemd/user"
userbin="$HOME/.local/bin"
mode=install
setup_args=()      # for auren setup
for arg in "$@"; do
  case "$arg" in
    --dev) mode=dev ;;
    --check) mode=check ;;
    --browser|--no-browser|--keys=super|--keys=alt|--no-keys|--yes|--brain=auto|--brain=claude|--brain=codex)
      setup_args+=("$arg") ;;
    *) echo "usage: $0 [--dev|--check] [--browser|--no-browser] [--keys=super|--keys=alt|--no-keys] [--brain=auto|claude|codex] [--yes]" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }

# ---- where it goes -----------------------------------------------------------------------------
# These paths end up in systemd units and sed replacements: keep them plain.
for path in "$share" "$config"; do
  [[ $path =~ ^/[A-Za-z0-9._/+-]+$ ]] || { warn "can't install with $path: use a folder without spaces or special characters"; exit 1; }
done
if [[ "$share/" == "$repo/"* || "$repo/" == "$share/"* ]]; then
  warn "the checkout ($repo) and the install folder ($share) overlap: clone Auren somewhere else"
  exit 1
fi

# ---- requirements ------------------------------------------------------------------------------
missing=0
for cmd in uv qs hyprctl pw-record pw-cat; do
  command -v "$cmd" >/dev/null || { warn "missing: $cmd"; missing=1; }
done
if [[ $mode == check ]]; then
  [[ -x "$share/venv/bin/auren" ]] && exec "$share/venv/bin/auren" doctor
  warn "Auren isn't installed yet: run ./install.sh"
  exit 1
fi
((missing == 0)) || { warn "install the missing commands first"; exit 1; }
if command -v pacman >/dev/null && pacman -Q auren >/dev/null 2>&1; then
  warn "the auren package is installed too: this install's services and commands take its place for you"
fi

# ---- Python environment ------------------------------------------------------------------------
say "Python environment: $share/venv"
mkdir -p "$share/bin"
sync=(uv sync --project "$repo" --frozen --no-dev)
# --reinstall-package: auren's version doesn't change between commits, so uv would keep the old copy
[[ $mode == dev ]] || sync+=(--no-editable --reinstall-package auren)
UV_PROJECT_ENVIRONMENT="$share/venv" "${sync[@]}"
ln -sfn "$share/venv/bin/auren-ctl" "$share/bin/auren-ctl"
ln -sfn "$share/venv/bin/auren" "$share/bin/auren"
mkdir -p "$userbin"
ln -sfn "$share/venv/bin/auren" "$userbin/auren"
ln -sfn "$share/venv/bin/auren-ctl" "$userbin/auren-ctl"
[[ ":$PATH:" == *":$userbin:"* ]] || warn "$userbin isn't in your PATH: run auren as $share/venv/bin/auren"

# ---- UI: mascot and bar plugin -----------------------------------------------------------------
place() { # place <source> <target>: a link in dev mode, a fresh copy otherwise
  rm -rf -- "$2"
  if [[ $mode == dev ]]; then ln -s "$1" "$2"; else cp -r "$1" "$2"; fi
}
[[ -e $share/bar-plugin ]] && had_plugin=1 || had_plugin=
place "$repo/mascot" "$share/mascot"
place "$repo/omarchy-plugin" "$share/bar-plugin"

# ---- systemd user services (auren setup starts them) ---------------------------------------------
mkdir -p "$units"
qs="$(command -v qs)"
[[ $qs =~ ^/[A-Za-z0-9._/+-]+$ ]] || { warn "qs is at $qs: a path without spaces or special characters is needed"; exit 1; }
# Folders other than the usual ones reach the services too (the user manager may not have them).
env=""
[[ $config == "$HOME/.config" ]] || env+="Environment=XDG_CONFIG_HOME=$config\n"
[[ $data == "$HOME/.local/share" ]] || env+="Environment=XDG_DATA_HOME=$data\n"
for unit in auren.service auren-mascot.service; do
  sed -e "s#@AUREN@#$share/venv/bin/auren#g" -e "s#@MASCOT@#$share/mascot#g" -e "s#@QS@#$qs#g" \
    -e "s#^@ENV@\$#$env#" "$repo/systemd/$unit" >"$units/$unit"
done
[[ -z $env ]] || warn "custom XDG folders: the bar plugin uses your session's, so set them there too"
systemctl --user daemon-reload 2>/dev/null || true

# ---- what's installed, for auren update ----------------------------------------------------------
# Written once the program is in place, before the per-user setup (which can be run again on its own).
if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
  dirty=false
  [[ -z "$(git -C "$repo" status --porcelain --untracked-files=no)" ]] || dirty=true
  "$share/venv/bin/python" - "$share/install.json" "$repo" "$mode" \
    "$(git -C "$repo" rev-parse HEAD)" "$(git -C "$repo" rev-parse --abbrev-ref HEAD)" \
    "$(git -C "$repo" log -1 --format=%ct)" "$(git -C "$repo" remote get-url origin 2>/dev/null || true)" \
    "$dirty" "$(git -C "$repo" describe --tags --exact-match HEAD 2>/dev/null || true)" <<'PY'
import json, os, sys, time
from importlib.metadata import version
path, source, mode, commit, branch, committed, remote, dirty, tag = sys.argv[1:]
with open(path + ".tmp", "w") as f:
    json.dump({"version": version("auren"), "commit": commit, "branch": branch, "committed": int(committed),
               "remote": remote, "source": source, "mode": mode, "dirty": dirty == "true",
               "tag": tag, "installed_at": int(time.time())}, f)
os.replace(path + ".tmp", path)
PY
else
  rm -f "$share/install.json"
  warn "not a git checkout: auren update can't tell which version this is"
fi

# ---- the per-user part: questions, talk keys, bar plugin, API key, voice model, services --------------
code=0
"$share/venv/bin/auren" setup "${setup_args[@]}" || code=$?
# the running shell keeps the panel it loaded (auren update restarts it itself)
[[ -z $had_plugin || -n ${AUREN_UPDATE:-} ]] || warn "the bar plugin was replaced: omarchy restart shell (loads the new panel)"
exit "$code"
