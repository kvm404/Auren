#!/usr/bin/env bash
# Render the bar panel's pages to PNGs, without touching the running bar or Auren:
#
#   tests/panel/preview.sh [out-dir] [shots]
#
# shots: a comma list of page[:state], pages 0-4 (Home, Mascot, Voice, Brain, Updates), states:
# available, confirm, updating, failed (Updates page), cursor, nav, down (the keyboard cursor; down goes to the page's last row).
# PANEL_MAX_HEIGHT=500 caps the panel's height like a short screen does.
#
# It runs a separate Quickshell off-screen with Omarchy's own UI parts, except the popup window and the
# bar button, which are drawn in place (tests/panel/Ui). Every XDG folder points to a temp dir with a
# sample state and history, so nothing it does (like checking for updates) reaches the real install.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$(realpath -m "${1:-$repo/.panel-preview}")"
shots="${2:-0,1,2,3,4,4:available,4:confirm,4:updating,4:failed,1:cursor}"
omarchy=/usr/share/omarchy/shell
[[ -d $omarchy/Ui ]] || { echo "needs the Omarchy shell ($omarchy)" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cfg="$tmp/shell"
mkdir -p "$cfg/Ui" "$tmp/data/auren" "$tmp/run/auren" "$tmp/state/auren" "$tmp/config/auren" "$out"
chmod 700 "$tmp/run"
ln -s "$omarchy/Commons" "$cfg/Commons"
ln -s "$omarchy/services" "$cfg/services"
for f in "$omarchy"/Ui/*; do
  [[ -e $here/Ui/$(basename "$f") ]] || ln -s "$f" "$cfg/Ui/"
done
cp "$here"/Ui/*.qml "$cfg/Ui/"
cp "$here/shell.qml" "$cfg/"
ln -s "$repo/omarchy-plugin" "$cfg/plugin"
ln -s "$repo/mascot" "$tmp/data/auren/mascot"
# Your Omarchy theme (read only), so the pages look as they will in your bar.
theme="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy"
[[ ! -d $theme ]] || ln -s "$theme" "$tmp/config/omarchy"
[[ ! -d ${XDG_RUNTIME_DIR:-}/hypr ]] || ln -s "$XDG_RUNTIME_DIR/hypr" "$tmp/run/hypr"   # corner rounding

# A sample Auren: idle, and alive (the panel checks the pid's command line for "auren").
bash -c 'exec -a auren-preview sleep 600' & keeper=$!
trap 'kill $keeper 2>/dev/null; rm -rf "$tmp"' EXIT
now=$(date +%s)
printf '{"state":"idle","detail":"","since":%s,"pid":%s,"voice":true,"voiceName":"F2","mode":"auto","effort":"low","tts":{"speed":1.05,"steps":5,"volume":1.0,"pause":0.1}}' \
  "$now" "$keeper" >"$tmp/run/auren/state.json"
printf '{"at":%s,"source":"voice","transcript":"Open Excalidraw.","reply":"Excalidraw is open now.","tools":["launch_app"],"think_s":3.2,"brain":"claude"}\n' \
  "$now" >"$tmp/state/auren/history.jsonl"
printf '{"version":"0.1.0","commit":"54ebe81000000000000000000000000000000000","committed":%s,"mode":"install"}' \
  "$((now - 86400))" >"$tmp/data/auren/install.json"

cd "$cfg"
SHOTS="$shots" OUT="$out" PANEL_MAX_HEIGHT="${PANEL_MAX_HEIGHT:-}" XDG_DATA_HOME="$tmp/data" XDG_RUNTIME_DIR="$tmp/run" XDG_STATE_HOME="$tmp/state" \
  XDG_CONFIG_HOME="$tmp/config" XDG_CACHE_HOME="$tmp/cache" QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
  timeout 120 qs -p "$cfg" 2>&1 | grep -E "ERROR|WARN.*qml|TypeError|ReferenceError" || true
echo "pages in $out:"
ls "$out"
