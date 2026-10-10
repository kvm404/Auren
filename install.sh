#!/usr/bin/env bash
# Install Auren for the current user on an Omarchy (Hyprland) laptop. Safe to run again,
# for example after moving the folder.
#
#   ./install.sh            set up everything below
#   ./install.sh --check    only report what's missing
#
# It installs the Python environment, the systemd user services (auren, auren-mascot), the
# Omarchy bar plugin (krvm.auren) and a secrets file to fill in. It doesn't edit your Hyprland
# config: it prints the key bindings and the layer rule to add.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
units="$config/systemd/user"
plugins="$config/omarchy/plugins"
check_only=false
[[ "${1:-}" == "--check" ]] && check_only=true

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }

# ---- requirements ------------------------------------------------------------------------------
missing=0
for cmd in uv qs socat wtype grim hyprctl; do
  command -v "$cmd" >/dev/null || { warn "missing: $cmd"; missing=1; }
done
for cmd in claude codex herdr obu; do
  command -v "$cmd" >/dev/null || warn "optional, not found: $cmd (see README)"
done
[[ -w /dev/uinput ]] || warn "/dev/uinput isn't writable: screen control (clicks, drags) won't work"
$check_only && exit "$missing"
((missing == 0)) || { warn "install the missing commands first"; exit 1; }

# ---- Python environment ------------------------------------------------------------------------
say "Python environment"
uv sync --project "$repo" --frozen

# ---- secrets -----------------------------------------------------------------------------------
mkdir -p "$config/auren"
if [[ ! -f "$config/auren/secrets.env" ]]; then
  say "Secrets file: $config/auren/secrets.env (fill in your keys)"
  install -m 600 /dev/null "$config/auren/secrets.env"
  printf '%s\n' "ASSEMBLYAI_API_KEY=" "ELEVENLABS_API_KEY=" >"$config/auren/secrets.env"
fi

# ---- systemd user services ---------------------------------------------------------------------
say "Services: auren, auren-mascot"
mkdir -p "$units"
for unit in auren.service auren-mascot.service; do
  sed "s#@REPO@#$repo#g" "$repo/systemd/$unit" >"$units/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now auren.service auren-mascot.service

# ---- Omarchy bar plugin ------------------------------------------------------------------------
if command -v omarchy >/dev/null; then
  say "Bar plugin: krvm.auren"
  mkdir -p "$plugins"
  ln -sfn "$repo/omarchy-plugin" "$plugins/krvm.auren"
  grep -q '"krvm.auren"' "$config/omarchy/shell.json" 2>/dev/null || omarchy plugin enable krvm.auren
  [[ "$repo" == "$HOME/Projects/auren" ]] ||
    warn "the plugin looks for ~/Projects/auren: set its \"aurenDir\" setting to $repo"
fi

# ---- Hyprland (printed, not edited) ------------------------------------------------------------
cat <<EOF

$(say "Add to ~/.config/hypr/bindings.lua (unbind anything already on these keys first):")
o.bind("SUPER + A", "Auren: talk (press again to send)", "$repo/bin/auren-ctl talk")
o.bind("SUPER + SHIFT + A", "Auren: talk in a fresh conversation", "$repo/bin/auren-ctl talk-fresh")

$(say "Add to ~/.config/hypr/hyprland.lua (so dragging the mascot follows the pointer):")
hl.layer_rule({ match = { namespace = "^auren-mascot" }, no_anim = true, animation = "none" })

EOF
say "Done. Press Super+A and talk."
