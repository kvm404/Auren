# Auren on macOS (preview)

Auren runs on a Mac too: the same voice, brains, tools and safety levels as on Omarchy, with macOS
doing the parts Hyprland, PipeWire and Quickshell do there. It's new and hasn't had much use yet, so
expect rough edges and please report them.

## Requirements

- An Apple Silicon Mac (M1 or newer) on macOS 14 Sonoma or newer. The voice model's runtime has no
  build for Intel Macs.
- [uv](https://docs.astral.sh/uv/): `brew install uv`.
- Claude Code or Codex, signed in.
- An [AssemblyAI](https://www.assemblyai.com/) API key.
- Optional: [herdr](https://herdr.dev) for coding agents in tabs (without it Auren starts them in a
  new terminal window), a Chromium browser (Chrome, Brave, Edge, Chromium, Helium or Vivaldi) for the
  browser tools, and `blueutil` (`brew install blueutil`) to turn Bluetooth on and off.

## Install

```bash
git clone https://github.com/kvm404/Auren.git auren
cd auren && ./install-macos.sh
```

The installer asks whether to set up the browser tools and which keys start Auren (Control+Option+A
by default, Control+Option+Shift+A for a fresh conversation). It installs into
`~/.local/share/auren`, links `auren` and `auren-ctl` into `~/.local/bin`, downloads the voice model
and adds two launch agents that start at login:

| Agent | Runs | Log |
|---|---|---|
| `com.auren.daemon` | `auren daemon`: listening, the brain, the tools, the talk keys | `~/.local/state/auren/daemon.out.log` |
| `com.auren.mascot` | `auren mascot`: the robot and the menu bar icon | `~/.local/state/auren/mascot.out.log` |

Then:

1. Put `ASSEMBLYAI_API_KEY=your-key` in `~/.config/auren/secrets.env` (or store it in the keychain:
   `security add-generic-password -s auren -a assemblyai_api_key -w`).
2. If you only use Codex, run `auren config set brain.mode codex`.
3. Run `auren service restart`, then `auren doctor`.

`./install-macos.sh --dev` links the checkout instead of copying it, for working on Auren.

## Permissions

macOS asks before a program can use the microphone, watch the keyboard, control the mouse or see the
screen. Auren needs:

| Permission | For |
|---|---|
| Microphone | Hearing you |
| Accessibility | The talk keys, clicking and typing, moving and closing windows |
| Screen Recording | Looking at the screen (the `look` tool) |
| Automation (asked per app) | Volume, Spotify and Music, dark mode, notifications, the terminal |

`auren permissions` asks for them. The program macOS asks about is the Python inside Auren's
environment (`auren permissions` prints its path), not "Auren": in **System Settings → Privacy &
Security**, turn on the entry for that Python under Accessibility and Screen Recording. If it isn't
listed, click **+**, press Cmd+Shift+G and paste the path. Restart Auren afterwards
(`auren service restart`): macOS applies Accessibility and Screen Recording to new processes only.

`auren doctor` shows which permissions are missing.

## Using it

Press **Control+Option+A** anywhere and talk; press it again to send (or let auto-send do it). The
robot sits in a corner of the screen and floats over every Space; drag it anywhere and scroll on it
to resize it. Its menu bar icon has the same controls as Omarchy's bar plugin: talk, stop, voice,
brain, safety level, mascot, and opening `auren doctor`.

`auren config set keys.talk cmd+shift+space` (and `keys.talk_fresh`) changes the keys; the daemon
picks them up without a restart.

## How the pieces map

| Omarchy | macOS |
|---|---|
| `pw-record` / `pw-cat` | PortAudio (`sounddevice`) |
| Hyprland bindings for Super+A | A Quartz event tap in the daemon (`keys.talk`) |
| `hyprctl` windows, focus, close | Quartz window list, Accessibility (AX) and `open -a` |
| Workspaces | Spaces, switched with Control+arrows (no API to move windows between Spaces) |
| `grim` + `ydotool`/`wtype` | `screencapture` + Quartz mouse and key events |
| `wl-copy` / `wl-paste` | NSPasteboard |
| systemd user units | launch agents |
| Quickshell mascot + bar plugin | `mascot/mac.qml` in a PySide6 host + a menu bar icon |
| `notify-send`, the OSD | Notification Center (`osascript`) |
| `$XDG_RUNTIME_DIR/auren` | `~/Library/Application Support/auren-runtime/auren` |
| Ctrl shortcuts in tools | Cmd shortcuts |

The code lives in `src/auren/mac/`. Each Linux module that differs ends with
`if sys.platform == "darwin": from ..mac.<name> import *`, and the Mac module's `__all__` lists exactly
what it replaces, so everything else (the brain, safety, speech, the tool definitions) is shared.

## Known limits

- Keyboard shortcuts are sent as US-layout key codes, so on other layouts some typed shortcuts may hit
  a different key. Text is pasted or typed as Unicode, so it isn't affected.
- Windows can't be moved to another Space; `move_window` only raises them.
- Brightness keys need a built-in display (or `brew install brightness`).
- The mascot's bubble is a copy of the one in `mascot/shell.qml`: change both.
- Permissions belong to the venv's Python, so updating Python (a new uv-managed Python) can make
  macOS ask again.

## Updating and removing

`auren update` works as on Omarchy. To remove Auren:

```bash
auren service uninstall
rm -rf ~/.local/share/auren ~/.local/bin/auren ~/.local/bin/auren-ctl
rm -rf ~/.config/auren ~/.local/state/auren ~/.cache/auren      # your settings and history too
```

Then remove the Python entries in System Settings → Privacy & Security.
