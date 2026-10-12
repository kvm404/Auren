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
config="${XDG_CONFIG_HOME:-$HOME/.config}"
share="${XDG_DATA_HOME:-$HOME/.local/share}/auren"
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
for unit in auren.service auren-mascot.service; do
  sed -e "s#@SHARE@#$share#g" -e "s#@QS@#$(command -v qs)#g" "$repo/systemd/$unit" >"$units/$unit"
done
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

# ---- Hyprland (printed, not edited) ------------------------------------------------------------
cat <<EOF

$(say "Add to ~/.config/hypr/bindings.lua (unbind anything already on these keys first):")
o.bind("SUPER + A", "Auren: talk (press again to send)", "$share/bin/auren-ctl talk")
o.bind("SUPER + SHIFT + A", "Auren: talk in a fresh conversation", "$share/bin/auren-ctl talk-fresh")

$(say "Add to ~/.config/hypr/hyprland.lua (so dragging the mascot follows the pointer):")
hl.layer_rule({ match = { namespace = "^auren-mascot" }, no_anim = true, animation = "none" })

EOF
"$share/venv/bin/auren" doctor || true
