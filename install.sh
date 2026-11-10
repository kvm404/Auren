#!/usr/bin/env bash
# Install Auren for the current user on Omarchy. Safe to run again: it's also how you update.
#
#   ./install.sh            install into ~/.local/share/auren (a copy: the checkout can move or go)
#   ./install.sh --dev      the same, but linked to this checkout: code changes need only a restart
#   ./install.sh --check    only report what's missing (auren doctor, once installed)
#   --browser / --no-browser   set up the browser tools (Open Browser Use) or turn them off, without asking
#   --keys=super / --keys=alt / --no-keys   add the talk keys to bindings.lua, or only print them, without asking
#
# Layout (see docs/architecture.md):
#   ~/.local/share/auren/venv/          the Python environment, with auren and auren-ctl
#   ~/.local/share/auren/mascot/        the floating mascot (Quickshell)
#   ~/.local/share/auren/bar-plugin/    the Omarchy bar plugin, linked as kvm404.auren
#   ~/.local/share/auren/bin/auren-ctl  what key bindings call
#   ~/.local/share/auren/models/        the voice model
#
# In a terminal it asks which talk keys to add to ~/.config/hypr/bindings.lua (backed up first);
# otherwise it prints them. It never touches the rest of your Hyprland config.
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
keys=ask
for arg in "$@"; do
  case "$arg" in
    --dev) mode=dev ;;
    --check) mode=check ;;
    --browser) browser=yes ;;
    --no-browser) browser=no ;;
    --keys=super) keys=super ;;
    --keys=alt) keys=alt ;;
    --no-keys) keys=print ;;
    *) echo "usage: $0 [--dev|--check] [--browser|--no-browser] [--keys=super|--keys=alt|--no-keys]" >&2; exit 2 ;;
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

# ---- which browser the browser tools would use --------------------------------------------------
# >>> browsers: the Chromium browsers Auren knows, in step with KINDS in src/auren/hands/browsers.py
# (tests/test_install.py checks). key|name|launchers (.desktop ids)|commands
chromium_browsers=(
  "chrome|Google Chrome|google-chrome google-chrome-stable com.google.Chrome|google-chrome-stable google-chrome"
  "chromium|Chromium|chromium chromium-browser org.chromium.Chromium|chromium chromium-browser"
  "brave|Brave|brave-browser brave com.brave.Browser|brave brave-browser"
  "helium|Helium|helium-browser helium net.imput.helium|helium-browser helium"
  "edge|Microsoft Edge|microsoft-edge microsoft-edge-stable|microsoft-edge-stable microsoft-edge"
  "vivaldi|Vivaldi|vivaldi-stable vivaldi|vivaldi-stable vivaldi"
)
# pick_browser: sets browser_key and browser_name (empty: no Chromium browser installed), and
# browser_is_default (yes when it's the default browser). Like `auren browser setup` with no browser
# named: the default browser if it's one of these, else the first one installed.
pick_browser() {
  local default="" entry key name ids cmds id cmd
  if command -v omarchy-cmd-default-browser >/dev/null; then default=$(omarchy-cmd-default-browser 2>/dev/null || true); fi
  if [[ -z $default || $default == omarchy-browser.desktop ]] && command -v xdg-settings >/dev/null; then
    default=$(xdg-settings get default-web-browser 2>/dev/null || true)
  fi
  [[ $default == omarchy-browser.desktop ]] && default=""
  default=${default%.desktop}
  browser_key="" browser_name="" browser_is_default=""
  for pass in default first; do
    for entry in "${chromium_browsers[@]}"; do
      IFS='|' read -r key name ids cmds <<<"$entry"
      local have=""
      for cmd in $cmds; do command -v "$cmd" >/dev/null && { have=1; break; }; done
      [[ -n $have ]] || continue
      if [[ $pass == default ]]; then
        for id in $ids; do
          if [[ $id == "$default" ]]; then browser_key=$key browser_name=$name browser_is_default=yes; return; fi
        done
      else
        browser_key=$key browser_name=$name
        return
      fi
    done
  done
}
# <<< browsers

# ---- the talk keys -----------------------------------------------------------------------------
# >>> keys (tests/test_install.py runs these)
bindings="$config/hypr/bindings.lua"
# keys_lines <super|alt>: Auren's lines for bindings.lua. Super+A frees Omarchy's own uses of the keys
# first: Hyprland runs every binding on a key, so without the unbinds both would fire.
keys_lines() {
  local ctl="$share/bin/auren-ctl"
  echo "-- Auren voice assistant: press to start talking, press again to send."
  if [[ $1 == super ]]; then
    echo 'hl.unbind("SUPER + A")          -- was: Select all (Omarchy 4.5; Ctrl+A still selects all)'
    echo 'hl.unbind("SUPER + SHIFT + A")  -- was: ChatGPT web app'
    echo "o.bind(\"SUPER + A\", \"Auren: talk (press again to send)\", \"$ctl talk\")"
    echo "o.bind(\"SUPER + SHIFT + A\", \"Auren: talk in a fresh conversation\", \"$ctl talk-fresh\")"
  else
    echo "o.bind(\"SUPER + ALT + A\", \"Auren: talk (press again to send)\", \"$ctl talk\")"
    echo "o.bind(\"SUPER + CTRL + ALT + A\", \"Auren: talk in a fresh conversation\", \"$ctl talk-fresh\")"
  fi
}
# keys_bound: a binding (not a comment) in some Hyprland config file already runs Auren's talk command.
# -R: dotfile managers link these files in.
keys_bound() {
  grep -RqsE --include='*.lua' --include='*.conf' \
    '^[[:space:]]*(o\.bind|hl\.bind|bind[a-z]*[[:space:]]*=).*auren-ctl talk' "$config/hypr"
}
# own_super_a: the user bound Super+A or Super+Shift+A to something of their own (Omarchy's defaults
# live elsewhere, so they don't count)
own_super_a() {
  grep -Eqs '^[[:space:]]*(o|hl)\.bind\([[:space:]]*"SUPER[[:space:]]*\+[[:space:]]*(SHIFT[[:space:]]*\+[[:space:]]*)?A"' "$bindings"
}
# add_keys <super|alt>: back up bindings.lua the way Omarchy does (bindings.lua.bak.<time>), then add
# Auren's lines at the end. Sets keys_backup.
add_keys() {
  keys_backup="$bindings.bak.$(date +%s)"
  cp -p "$bindings" "$keys_backup"
  { echo; keys_lines "$1"; } >>"$bindings"
}
# <<< keys

# ---- questions, asked now so the rest runs on its own ------------------------------------------
# Browser tools: kept as they are when they already work (or were turned off), and never asked on an update.
if [[ $browser == ask && -n ${AUREN_UPDATE:-} ]]; then
  browser=keep
elif [[ $browser == ask && -x $share/venv/bin/auren ]] && "$share/venv/bin/auren" browser status >/dev/null 2>&1; then
  browser=keep
fi
if [[ $browser == ask || $browser == yes ]]; then
  pick_browser
  if [[ -z $browser_key ]]; then
    # Nothing to set it up in: don't ask, and say why once.
    [[ $browser == yes ]] && warn "no Chromium browser is installed (Chrome, Chromium, Brave, Helium, Edge or Vivaldi): skipping the browser tools"
    browser=none
  elif [[ $browser == ask && -t 0 ]]; then
    say "Browser tools (optional)"
    echo "Auren can work in your own logged-in browser: read pages, click through sites, fill forms, download."
    if [[ -n $browser_is_default ]]; then
      echo "This sets them up in $browser_name, your default browser: it downloads Open Browser Use and opens"
    else
      echo "Your default browser isn't one Auren can use (it needs a Chromium browser), so this sets them up in"
      echo "$browser_name, which is installed too: it downloads Open Browser Use and opens"
    fi
    echo "its extension's page there, where you click Add."
    read -rp "Set up the browser tools in $browser_name? [Y/n] " answer || answer=n   # end of input: no
    case "${answer,,}" in ""|y|yes) browser=yes ;; *) browser=no ;; esac
  elif [[ $browser == ask ]]; then
    browser=later
  fi
fi

# Talk keys: never added twice, nor on an update; without a terminal (or bindings.lua) they're printed.
if [[ $keys != print ]] && keys_bound; then
  keys=keep
elif [[ ! -f $bindings ]]; then
  [[ $keys == ask ]] || warn "no $bindings to add the talk keys to: they're printed at the end"
  keys=print
elif [[ $keys == ask && ( ! -t 0 || -n ${AUREN_UPDATE:-} ) ]]; then
  keys=print
elif [[ $keys == ask ]]; then
  if own_super_a; then suggest=2; else suggest=1; fi
  say "Talk keys"
  echo "Auren starts listening when you press its key. This adds the key to $bindings"
  echo "(after backing it up):"
  echo "  1) Super+A, and Super+Shift+A for a fresh conversation. They replace whatever is on those"
  echo "     keys: on Omarchy, Select all (Ctrl+A still works) and the ChatGPT app."
  echo "  2) Super+Alt+A and Super+Ctrl+Alt+A, keeping Omarchy's keys as they are."
  echo "  3) Skip: print the lines and I'll add them myself."
  read -rp "Which keys? [$suggest] " answer || answer=3   # end of input: skip
  case "${answer:-$suggest}" in 1) keys=super ;; 2) keys=alt ;; *) keys=print ;; esac
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
    "$share/venv/bin/auren" browser setup "$browser_key" --yes || warn "the browser tools aren't working yet: auren browser setup"
    ;;
  no)
    "$share/venv/bin/auren" config set integrations.browser false >/dev/null
    say "Browser tools: off (auren browser setup turns them on)"
    ;;
  later)
    warn "the browser tools aren't set up: run auren browser setup $browser_key (or ./install.sh --browser)"
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

# ---- Hyprland: the talk keys --------------------------------------------------------------------
case $keys in
  super|alt)
    add_keys "$keys"
    say "Talk keys: added to $bindings (the old file is ${keys_backup##*/})"
    keys_lines "$keys" | sed -n 's/^o\.bind("\([^"]*\)", "Auren: \([^"]*\)".*/  \1: \2/p'
    hyprctl reload >/dev/null 2>&1 || true   # in case automatic reloading is off
    ;;
  keep)
    say "Talk keys: already in your Hyprland config"
    ;;
  print)
    echo
    say "Add to $bindings:"
    keys_lines super
    say "To keep Omarchy's Select all and ChatGPT keys, use these instead:"
    keys_lines alt | tail -n +2
    ;;
esac
cat <<EOF

$(say "Optional: add to ~/.config/hypr/hyprland.lua to skip mascot layer animations:")
hl.layer_rule({ match = { namespace = "^auren-mascot" }, no_anim = true, animation = "none" })

EOF
"$share/venv/bin/auren" doctor || true
