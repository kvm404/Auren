# Auren on macOS (preview)

Auren runs on a Mac too: the same voice, brains, tools and safety levels as on Omarchy, with macOS
doing the parts Hyprland, PipeWire and Quickshell do there. It's new and hasn't had much use yet, so
expect rough edges and please report them.

## Requirements

- An Apple Silicon Mac (M1 or newer) on macOS 14 Sonoma or newer. The voice model's runtime has no
  build for Intel Macs.
- [uv](https://docs.astral.sh/uv/): `brew install uv`.
- Claude Code or Codex, signed in (`brew install --cask codex`, then `codex` once).
- Apple's Command Line Tools, to build Auren.app (Homebrew installs them; else `xcode-select --install`).
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
`~/.local/share/auren`, links `auren` and `auren-ctl` into `~/.local/bin`, downloads the voice model,
builds **Auren.app** in `~/Applications`, and adds two launch agents that start at login and run
Auren through the app:

| Agent | Runs | Log |
|---|---|---|
| `com.auren.daemon` | `Auren daemon`: listening, the brain, the tools, the talk keys | `~/.local/state/auren/daemon.out.log` |
| `com.auren.mascot` | `Auren mascot`: the robot, the menu bar icon, the setup window | `~/.local/state/auren/mascot.out.log` |

Then it opens Auren, and Auren's setup window takes over: each step has one button.

| Step | |
|---|---|
| Microphone | **Allow**, then allow in the macOS prompt |
| Accessibility | **Allow** opens the Settings list with Auren already in it: switch it on |
| Screen Recording | Optional, the same way; Auren restarts to use it |
| Speech to text | Paste an AssemblyAI key and **Save** |
| Brain | Shows Claude Code or Codex; if neither is installed, copies the install command |

The window comes back from the menu bar icon (**Set up Auren…**), from opening Auren.app in Finder or
Spotlight, or with `auren open`. `auren doctor` checks the same things in a terminal.

`./install-macos.sh --dev` links the checkout instead of copying it, for working on Auren.

## Auren.app

macOS gives a permission to the app responsible for a process. Auren is Python, so without an app
Settings lists a `python3.12` in a hidden folder, and any Python update loses the permissions. Auren.app
is a tiny launcher (`src/auren/mac/app/launcher.c`) with Auren's name, icon and the text of its
permission prompts. It runs Auren's Python as its child, so Settings lists **Auren**, and Python can
change underneath without macOS noticing.

It's signed ad hoc on your Mac, and macOS ties the permissions to that signature, so the installer
rebuilds it only when the launcher's sources change (`auren app status` says whether they did).
Updating Auren's Python code never touches it. The icon is the Auren robot, rendered from
`mascot/app-icon.qml` by `python -m auren.mac.app icon`.

| Permission | For |
|---|---|
| Microphone | Hearing you |
| Accessibility | The talk keys, clicking and typing, moving and closing windows |
| Screen Recording | Looking at the screen (the `look` tool) |
| Automation (asked per app) | Volume, Spotify and Music, dark mode, notifications, the terminal |

## Using it

Press **Control+Option+A** anywhere and talk; press it again to send (or let auto-send do it). The
robot sits in a corner of the screen and floats over every Space; drag it anywhere and scroll on it
to resize it. Its menu bar icon has the same controls as Omarchy's bar plugin: talk, stop, voice,
brain, safety level, mascot, and the setup window.

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
| systemd user units | launch agents, running through Auren.app |
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
- Auren.app is signed ad hoc, not by a registered developer, so it can't be passed to another Mac as
  a download yet: each Mac builds its own with the installer.

## Updating and removing

`auren update` works as on Omarchy. To remove Auren:

```bash
auren service uninstall
auren app uninstall
rm -rf ~/.local/share/auren ~/.local/bin/auren ~/.local/bin/auren-ctl
rm -rf ~/.config/auren ~/.local/state/auren ~/.cache/auren      # your settings and history too
```

Then remove Auren from the lists in System Settings → Privacy & Security.
