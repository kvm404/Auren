#!/usr/bin/env bash
# Install Auren for the current user on Omarchy. Safe to run again: it's also how you update.
#
#   ./install.sh            install into ~/.local/share/auren (a copy: the checkout can move or go)
#   ./install.sh --dev      the same, but linked to this checkout: code changes need only a restart
#   ./install.sh --check    only report what's missing (auren doctor, once installed)
#   --browser / --no-browser   set up the browser tools (Open Browser Use) or turn them off, without asking
#
# Layout (see docs/architecture.md):
#   ~/.local/share/auren/venv/          the Python environment, with auren and auren-ctl
#   ~/.local/share/auren/mascot/        the floating mascot (Quickshell)
#   ~/.local/share/auren/bar-plugin/    the Omarchy bar plugin, linked as kvm404.auren
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
browser=ask
for arg in "$@"; do
  case "$arg" in
    --dev) mode=dev ;;
    --check) mode=check ;;
    --browser) browser=yes ;;
    --no-browser) browser=no ;;
    *) echo "usage: $0 [--dev|--check] [--browser|--no-browser]" >&2; exit 2 ;;
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

# ---- questions, asked now so the rest runs on its own ------------------------------------------
# Browser tools: kept as they are when they already work (or were turned off), and never asked on an update.
if [[ $browser == ask && -n ${AUREN_UPDATE:-} ]]; then
  browser=keep
elif [[ $browser == ask ]]; then
  if [[ -x $share/venv/bin/auren ]] && "$share/venv/bin/auren" browser status >/dev/null 2>&1; then
    browser=keep
  elif [[ -t 0 ]]; then
    say "Browser tools (optional)"
    echo "Auren can work in your own logged-in browser: read pages, click through sites, fill forms, download."
    echo "This downloads Open Browser Use and opens its extension's page in your browser, where you click Add."
    read -rp "Set up the browser tools? [Y/n] " answer
    case "${answer,,}" in ""|y|yes) browser=yes ;; *) browser=no ;; esac
  else
    browser=later
  fi
fi

# ---- Python environment ------------------------------------------------------------------------
say "Python environment: $share/venv"
mkdir -p "$share/bin"
sync=(uv sync --project "$repo" --frozen --no-dev)
# --reinstall-package: auren's version doesn't change between commits, so uv would keep the old copy
[[ $mode == dev ]] || sync+=(--no-editable --reinstall-package auren)
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
[[ -e $share/bar-plugin ]] && had_plugin=1 || had_plugin=
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
  if [[ -n ${AUREN_UPDATE:-} ]]; then
    :   # auren update stopped them, and starts them again as they were
  elif systemctl --user is-active --quiet "$unit"; then
    systemctl --user enable "$unit" >/dev/null 2>&1
    warn "$unit is running the old version: systemctl --user restart $unit (when Auren is idle)"
  else
    systemctl --user enable --now "$unit"
  fi
done

# ---- Omarchy bar plugin ------------------------------------------------------------------------
if command -v omarchy >/dev/null; then
  plugin=kvm404.auren old_plugin=krvm.auren shell_json="$config/omarchy/shell.json"
  say "Bar plugin: $plugin"
  mkdir -p "$plugins"
  ln -sfn "$share/bar-plugin" "$plugins/$plugin"
  if [[ -L $plugins/$old_plugin ]]; then
    # Up to 0.1.3 the plugin was krvm.auren: put the new one where it was, then remove it.
    if grep -q "\"$old_plugin\"" "$shell_json" 2>/dev/null; then
      say "Renaming the bar plugin $old_plugin to $plugin"
      if omarchy-shell shell ping >/dev/null 2>&1; then
        # The running shell owns shell.json: ask it. The new id needs a rescan, which runs in the
        # background, so retry while it says "not ready".
        omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
        for _ in $(seq 50); do
          result=$(omarchy-shell shell putBarWidget "$plugin" "{\"after\":\"$old_plugin\"}" 2>&1) || true
          [[ $result == "not ready" ]] || break
          sleep 0.1
        done
        [[ $result == ok ]] || warn "couldn't place $plugin in the bar ($result): omarchy plugin enable $plugin"
        omarchy plugin disable "$old_plugin" >/dev/null 2>&1 || true
      else
        # No shell running: rename the entries in place, keeping their position and settings.
        "$share/venv/bin/python" - "$shell_json" "$old_plugin" "$plugin" <<'PY'
import json, os, sys
path, old, new = sys.argv[1:]
with open(path) as f:
    config = json.load(f)
def rename(node):
    if isinstance(node, dict):
        if node.get("id") == old:
            node["id"] = new
        for value in node.values():
            rename(value)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            if value == old:
                node[i] = new
            else:
                rename(value)
rename(config)
with open(path + ".auren-tmp", "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
os.replace(path + ".auren-tmp", path)
PY
      fi
    fi
    rm -f "$plugins/$old_plugin"
    omarchy-shell -q shell rescanPlugins
  elif ! grep -q "\"$plugin\"" "$shell_json" 2>/dev/null; then
    omarchy plugin enable "$plugin"
  fi
  # the running shell keeps the panel it loaded (auren update restarts it itself)
  [[ -z $had_plugin || -n ${AUREN_UPDATE:-} ]] || warn "the bar plugin was replaced: omarchy restart shell (loads the new panel)"
fi

# ---- browser tools (Open Browser Use) -----------------------------------------------------------
case $browser in
  yes)
    say "Browser tools"
    "$share/venv/bin/auren" browser setup --yes || warn "the browser tools aren't working yet: auren browser setup"
    ;;
  no)
    "$share/venv/bin/auren" config set integrations.browser false >/dev/null
    say "Browser tools: off (auren browser setup turns them on)"
    ;;
  later)
    warn "the browser tools aren't set up: run auren browser setup (or ./install.sh --browser)"
    ;;
esac

# ---- what's installed, for auren update ----------------------------------------------------------
# Written last, so it only names an install that finished.
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

# ---- Hyprland (printed, not edited) ------------------------------------------------------------
cat <<EOF

$(say "Add to ~/.config/hypr/bindings.lua (the unbinds free Omarchy's own uses of these keys; unbinding a free key is harmless):")
hl.unbind("SUPER + A")          -- was: Select all (Omarchy 4.5; Ctrl+A still selects all)
hl.unbind("SUPER + SHIFT + A")  -- was: ChatGPT web app
o.bind("SUPER + A", "Auren: talk (press again to send)", "$share/bin/auren-ctl talk")
o.bind("SUPER + SHIFT + A", "Auren: talk in a fresh conversation", "$share/bin/auren-ctl talk-fresh")
$(say "To keep those, use SUPER + ALT + A and SUPER + CTRL + ALT + A instead (free on Omarchy 4.5), without the unbinds.")

$(say "Optional: add to ~/.config/hypr/hyprland.lua to skip mascot layer animations:")
hl.layer_rule({ match = { namespace = "^auren-mascot" }, no_anim = true, animation = "none" })

EOF
"$share/venv/bin/auren" doctor || true
