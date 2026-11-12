#!/usr/bin/env bash
# Install Auren for the current user on macOS (Apple Silicon, macOS 14 or newer). Safe to run again:
# it's also how you update. install.sh is the Omarchy installer; this one does the same on a Mac.
#
#   ./install-macos.sh            install into ~/.local/share/auren (a copy: the checkout can move or go)
#   ./install-macos.sh --dev      the same, but linked to this checkout: code changes need only a restart
#   ./install-macos.sh --check    only report what's missing (auren doctor, once installed)
#   --browser / --no-browser      set up the browser tools (Open Browser Use) or turn them off, without asking
#   --keys=default / --no-keys    use Ctrl+Option+A as the talk key, or leave the keys as they are, without asking
#
# Layout (see docs/macos.md):
#   ~/.local/share/auren/venv/          the Python environment, with auren and auren-ctl
#   ~/.local/share/auren/mascot/        the mascot's QML (run by `auren mascot`, with PySide6)
#   ~/.local/share/auren/bin/auren-ctl  what the mascot and the menu call
#   ~/.local/share/auren/models/        the voice model
#   ~/Library/LaunchAgents/com.auren.daemon.plist, com.auren.mascot.plist   start at login
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
share="$data/auren"
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
    --keys=default) keys=default ;;
    --no-keys) keys=keep ;;
    *) echo "usage: $0 [--dev|--check] [--browser|--no-browser] [--keys=default|--no-keys]" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }

# ---- requirements ------------------------------------------------------------------------------
[[ $(uname -s) == Darwin ]] || { warn "this is the macOS installer: on Omarchy run ./install.sh"; exit 1; }
if [[ $mode == check ]]; then
  [[ -x "$share/venv/bin/auren" ]] && exec "$share/venv/bin/auren" doctor
  warn "Auren isn't installed yet: run ./install-macos.sh"
  exit 1
fi
[[ $(uname -m) == arm64 ]] || { warn "Auren's voice needs an Apple Silicon Mac (this one is $(uname -m))"; exit 1; }
macos=$(sw_vers -productVersion)
(( ${macos%%.*} >= 14 )) || { warn "Auren needs macOS 14 or newer (this is $macos)"; exit 1; }
if ! command -v uv >/dev/null; then
  warn "uv is missing: install it with  brew install uv  or  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi
for path in "$share" "$config"; do
  [[ $path =~ ^/[A-Za-z0-9._/+-]+$ ]] || { warn "can't install with $path: use a folder without spaces or special characters"; exit 1; }
done
if [[ "$share/" == "$repo/"* || "$repo/" == "$share/"* ]]; then
  warn "the checkout ($repo) and the install folder ($share) overlap: clone Auren somewhere else"
  exit 1
fi
command -v claude >/dev/null || command -v codex >/dev/null ||
  warn "neither Claude Code nor Codex is installed: Auren needs one of them as its brain"

# ---- questions, asked now so the rest runs on its own ------------------------------------------
# >>> browsers: in step with KINDS in src/auren/mac/browsers.py (tests/test_install.py checks)
mac_browsers=("chrome|Google Chrome" "chromium|Chromium" "brave|Brave Browser" "helium|Helium" "edge|Microsoft Edge"
              "vivaldi|Vivaldi")
# <<< browsers
installed_browsers=()
for entry in "${mac_browsers[@]}"; do
  IFS='|' read -r key app <<<"$entry"
  [[ -d "/Applications/$app.app" || -d "$HOME/Applications/$app.app" ]] && installed_browsers+=("$app")
done
if [[ $browser == ask && -n ${AUREN_UPDATE:-} ]]; then
  browser=keep
elif [[ $browser == ask && -x $share/venv/bin/auren ]] && "$share/venv/bin/auren" browser status >/dev/null 2>&1; then
  browser=keep
fi
if [[ $browser == ask || $browser == yes ]] && ((${#installed_browsers[@]} == 0)); then
  [[ $browser == yes ]] && warn "no Chromium browser is installed (Chrome, Chromium, Brave, Helium, Edge or Vivaldi): skipping the browser tools"
  browser=none
elif [[ $browser == ask && -t 0 ]]; then
  say "Browser tools (optional)"
  echo "Auren can work in your own logged-in browser: read pages, click through sites, fill forms, download."
  echo "This downloads Open Browser Use and opens its extension's page in ${installed_browsers[0]} (or your"
  echo "default browser, if it's a Chromium one), where you click Add."
  read -rp "Set up the browser tools? [Y/n] " answer || answer=n
  case "$(tr '[:upper:]' '[:lower:]' <<<"$answer")" in ""|y|yes) browser=yes ;; *) browser=no ;; esac
elif [[ $browser == ask ]]; then
  browser=later
fi

if [[ $keys == ask && ( ! -t 0 || -n ${AUREN_UPDATE:-} ) ]]; then
  keys=keep
elif [[ $keys == ask ]]; then
  say "Talk keys"
  echo "Auren starts listening when you press its key, anywhere on the Mac."
  echo "  1) Control+Option+A, and Control+Option+Shift+A for a fresh conversation."
  echo "  2) Others: type them, like cmd+shift+space"
  read -rp "Which keys? [1] " answer || answer=1
  if [[ ${answer:-1} == 2 ]]; then
    read -rp "Talk key: " talk_key || talk_key=""
    read -rp "Fresh conversation key: " fresh_key || fresh_key=""
    keys=custom
  else
    keys=default
  fi
fi

# ---- Python environment ------------------------------------------------------------------------
say "Python environment: $share/venv"
mkdir -p "$share/bin"
sync=(uv sync --project "$repo" --frozen --no-dev)
[[ $mode == dev ]] || sync+=(--no-editable --reinstall-package auren)
UV_PROJECT_ENVIRONMENT="$share/venv" "${sync[@]}"
ln -sfn "$share/venv/bin/auren-ctl" "$share/bin/auren-ctl"
mkdir -p "$userbin"
ln -sfn "$share/venv/bin/auren" "$userbin/auren"
ln -sfn "$share/venv/bin/auren-ctl" "$userbin/auren-ctl"
[[ ":$PATH:" == *":$userbin:"* ]] || warn "$userbin isn't in your PATH: add it to ~/.zprofile, or run $share/venv/bin/auren"
auren="$share/venv/bin/auren"

# ---- the mascot's files ------------------------------------------------------------------------
rm -rf -- "$share/mascot"
if [[ $mode == dev ]]; then ln -s "$repo/mascot" "$share/mascot"; else cp -R "$repo/mascot" "$share/mascot"; fi

# ---- secrets and settings ----------------------------------------------------------------------
mkdir -p "$config/auren"
if [[ ! -f "$config/auren/secrets.env" ]]; then
  install -m 600 /dev/null "$config/auren/secrets.env"
  echo "ASSEMBLYAI_API_KEY=" >"$config/auren/secrets.env"
  warn "add your AssemblyAI key to $config/auren/secrets.env"
fi
case $keys in
  default)
    "$auren" config set keys.talk ctrl+alt+a >/dev/null
    "$auren" config set keys.talk_fresh ctrl+alt+shift+a >/dev/null ;;
  custom)
    [[ -z $talk_key ]] || "$auren" config set keys.talk "$talk_key" >/dev/null || warn "not a shortcut: $talk_key (kept the old one)"
    [[ -z $fresh_key ]] || "$auren" config set keys.talk_fresh "$fresh_key" >/dev/null || warn "not a shortcut: $fresh_key (kept the old one)" ;;
esac

# ---- voice model -------------------------------------------------------------------------------
say "Voice model"
"$auren" voice-model || warn "no voice model: Auren will answer in text until 'auren voice-model' works"

# ---- launch agents -----------------------------------------------------------------------------
say "Services: com.auren.daemon, com.auren.mascot"
# The PATH they get: this shell's, so the brains (claude, codex), herdr and Homebrew's tools are found.
AUREN_PATH="$PATH" "$auren" service install >/dev/null
if [[ -n ${AUREN_UPDATE:-} ]]; then
  :   # auren update stopped them, and starts them again as they were
elif "$auren" service status | grep -q running; then
  warn "Auren is running the old version: auren service restart (when Auren is idle)"
else
  "$auren" service start || warn "the services didn't start: see ~/.local/state/auren/daemon.out.log"
fi

# ---- what's installed, for auren update ----------------------------------------------------------
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

# ---- browser tools -------------------------------------------------------------------------------
case $browser in
  yes)
    say "Browser tools"
    "$auren" browser setup --yes || warn "the browser tools aren't working yet: auren browser setup" ;;
  no)
    "$auren" config set integrations.browser false >/dev/null
    say "Browser tools: off (auren browser setup turns them on)" ;;
  later)
    warn "the browser tools aren't set up: run auren browser setup (or ./install-macos.sh --browser)" ;;
esac

# ---- permissions -------------------------------------------------------------------------------
if [[ -z ${AUREN_UPDATE:-} && -t 0 ]]; then
  say "Permissions"
  echo "macOS asks before an app can use the microphone, the keyboard and mouse, or see the screen."
  echo "Auren asks now: allow each one (in Settings, turn on the Python that Auren runs as)."
  sleep 2
  "$auren" permissions || warn "run auren permissions once Auren is running"
fi

echo
"$auren" doctor || true
