#!/usr/bin/env bash
# Install Auren for the current user on Omarchy. Safe to run again: it's also how you update.
#
#   ./install.sh            install into ~/.local/share/auren (a copy: the checkout can move or go)
#   ./install.sh --dev      the same, but linked to this checkout: code changes need only a restart
#   ./install.sh --check    only report what's missing (auren doctor, once installed)
#
# Layout (see docs/architecture.md):
#   ~/.local/share/auren/venv/          the Python environment, with auren and auren-ctl
#   ~/.local/share/auren/mascot/        the floating mascot (Quickshell)
#   ~/.local/share/auren/bar-plugin/    the Omarchy bar plugin, linked as krvm.auren
#   ~/.local/share/auren/bin/auren-ctl  what key bindings call
#   ~/.local/share/auren/models/        the voice model
#
# It doesn't edit your Hyprland config: it prints the key bindings and the layer rule to add.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config="$(realpath -m "${XDG_CONFIG_HOME:-$HOME/.config}")"
data="$(realpath -m "${XDG_DATA_HOME:-$HOME/.local/share}")"
share="$data/auren"
units="$config/systemd/user"
plugins="$config/omarchy/plugins"
userbin="$HOME/.local/bin"
mode=install
case "${1:-}" in
  --dev) mode=dev ;;
  --check) mode=check ;;
  "") ;;
  *) echo "usage: $0 [--dev|--check]" >&2; exit 2 ;;
esac

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

# ---- Python environment ------------------------------------------------------------------------
say "Python environment: $share/venv"
mkdir -p "$share/bin"
sync=(uv sync --project "$repo" --frozen --no-dev)
[[ $mode == dev ]] || sync+=(--no-editable)
UV_PROJECT_ENVIRONMENT="$share/venv" "${sync[@]}"
ln -sfn "$share/venv/bin/auren-ctl" "$share/bin/auren-ctl"
mkdir -p "$userbin"
ln -sfn "$share/venv/bin/auren" "$userbin/auren"
ln -sfn "$share/venv/bin/auren-ctl" "$userbin/auren-ctl"
[[ ":$PATH:" == *":$userbin:"* ]] || warn "$userbin isn't in your PATH: run auren as $share/venv/bin/auren"

# ---- UI: mascot and bar plugin -----------------------------------------------------------------
place() { # place <source> <target>: a link in dev mode, a fresh copy otherwise
  rm -rf -- "$2"
  if [[ $mode == dev ]]; then ln -s "$1" "$2"; else cp -r "$1" "$2"; fi
}
place "$repo/mascot" "$share/mascot"
place "$repo/omarchy-plugin" "$share/bar-plugin"

# ---- secrets -----------------------------------------------------------------------------------
mkdir -p "$config/auren"
if [[ ! -f "$config/auren/secrets.env" ]]; then
  install -m 600 /dev/null "$config/auren/secrets.env"
  echo "ASSEMBLYAI_API_KEY=" >"$config/auren/secrets.env"
  warn "add your AssemblyAI key to $config/auren/secrets.env"
fi

# ---- voice model -------------------------------------------------------------------------------
say "Voice model"
"$share/venv/bin/auren" voice-model || warn "no voice model: Auren will answer in text until 'auren voice-model' works"

# ---- systemd user services ---------------------------------------------------------------------
say "Services: auren, auren-mascot"
mkdir -p "$units"
qs="$(command -v qs)"
[[ $qs =~ ^/[A-Za-z0-9._/+-]+$ ]] || { warn "qs is at $qs: a path without spaces or special characters is needed"; exit 1; }
# Folders other than the usual ones reach the services too (the user manager may not have them).
env=""
[[ $config == "$HOME/.config" ]] || env+="Environment=XDG_CONFIG_HOME=$config\n"
[[ $data == "$HOME/.local/share" ]] || env+="Environment=XDG_DATA_HOME=$data\n"
for unit in auren.service auren-mascot.service; do
  sed -e "s#@SHARE@#$share#g" -e "s#@QS@#$qs#g" -e "s#^@ENV@\$#$env#" "$repo/systemd/$unit" >"$units/$unit"
done
[[ -z $env ]] || warn "custom XDG folders: the bar plugin uses your session's, so set them there too"
systemctl --user daemon-reload
for unit in auren auren-mascot; do
  if systemctl --user is-active --quiet "$unit"; then
    systemctl --user enable "$unit" >/dev/null 2>&1
    warn "$unit is running the old version: systemctl --user restart $unit (when Auren is idle)"
  else
    systemctl --user enable --now "$unit"
  fi
done

# ---- Omarchy bar plugin ------------------------------------------------------------------------
if command -v omarchy >/dev/null; then
  say "Bar plugin: krvm.auren"
  mkdir -p "$plugins"
  ln -sfn "$share/bar-plugin" "$plugins/krvm.auren"
  grep -q '"krvm.auren"' "$config/omarchy/shell.json" 2>/dev/null || omarchy plugin enable krvm.auren
fi

# ---- what's installed, for auren update ----------------------------------------------------------
# Written last, so it only names an install that finished.
if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
  "$share/venv/bin/python" - "$share/install.json" "$repo" "$mode" \
    "$(git -C "$repo" rev-parse HEAD)" "$(git -C "$repo" rev-parse --abbrev-ref HEAD)" \
    "$(git -C "$repo" log -1 --format=%ct)" "$(git -C "$repo" remote get-url origin 2>/dev/null || true)" <<'PY'
import json, sys, time
from importlib.metadata import version
path, source, mode, commit, branch, committed, remote = sys.argv[1:]
with open(path, "w") as f:
    json.dump({"version": version("auren"), "commit": commit, "branch": branch, "committed": int(committed),
               "remote": remote, "source": source, "mode": mode, "installed_at": int(time.time())}, f)
PY
else
  rm -f "$share/install.json"
  warn "not a git checkout: auren update can't tell which version this is"
fi

# ---- Hyprland (printed, not edited) ------------------------------------------------------------
cat <<EOF

$(say "Add to ~/.config/hypr/bindings.lua (unbind anything already on these keys first):")
o.bind("SUPER + A", "Auren: talk (press again to send)", "$share/bin/auren-ctl talk")
o.bind("SUPER + SHIFT + A", "Auren: talk in a fresh conversation", "$share/bin/auren-ctl talk-fresh")

$(say "Add to ~/.config/hypr/hyprland.lua (so dragging the mascot follows the pointer):")
hl.layer_rule({ match = { namespace = "^auren-mascot" }, no_anim = true, animation = "none" })

EOF
"$share/venv/bin/auren" doctor || true
