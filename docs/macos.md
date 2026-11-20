# Auren on macOS (preview)

Auren runs on a Mac too: the same voice, brains, tools and safety levels as on Omarchy, with macOS
doing the parts Hyprland, PipeWire and Quickshell do there. It's new and hasn't had much use yet, so
expect rough edges and please report them.

## Requirements

- An Apple Silicon Mac (M1 or newer) on macOS 14 Sonoma or newer. The voice model's runtime has no
  build for Intel Macs.
- [Homebrew](https://brew.sh). It brings [uv](https://docs.astral.sh/uv/), which builds Auren's
  Python environment (installing from a clone instead needs `brew install uv`).
- Claude Code or Codex, signed in (`brew install --cask codex`, then `codex` once).
- Apple's Command Line Tools, to build Auren.app (Homebrew installs them; else `xcode-select --install`).
- An [AssemblyAI](https://www.assemblyai.com/) API key.
- Optional: [herdr](https://herdr.dev) for coding agents in tabs (without it Auren starts them in a
  new terminal window), a Chromium browser (Chrome, Brave, Edge, Chromium, Helium or Vivaldi) for the
  browser tools, and `blueutil` (`brew install blueutil`) to turn Bluetooth on and off.

## Install

```bash
brew install kvm404/auren/auren
auren setup
```

Homebrew installs Auren's source and the `auren` and `auren-ctl` commands. `auren setup` does the rest
for your user, which Homebrew can't: it asks whether to set up the browser tools and which keys start
Auren (Control+Option+A by default, Control+Option+Shift+A for a fresh conversation). It builds Auren's
Python environment in `~/.local/share/auren`, downloads the voice model, builds **Auren.app** in
`~/Applications`, and adds two launch agents that start at login and run Auren through the app:

| Agent | Runs | Log |
|---|---|---|
| `com.auren.daemon` | `Auren daemon`: listening, the brain, the tools, the talk keys | `~/.local/state/auren/daemon.out.log` |
| `com.auren.mascot` | `Auren mascot`: the robot, the menu bar icon, the setup window and the Auren window | `~/.local/state/auren/mascot.out.log` |

Then it opens Auren, and Auren's setup window takes over: each step has one button.

| Step | |
|---|---|
| Microphone | **Allow**, then allow in the macOS prompt |
| Accessibility | **Allow** opens the Settings list with Auren already in it: switch it on |
| Screen Recording | Optional, the same way |
| Speech to text | Paste an AssemblyAI key and **Save** |
| Brain | Shows Claude Code or Codex; if neither is installed, copies the install command |

Each step notices by itself when you allow something in System Settings: there's nothing to restart.
macOS lets a running program use the microphone or Screen Recording only from its next start (and can
keep saying no to Accessibility after it was turned off and on again), so for those Auren checks with a
new process, and when that one has the permission, Auren restarts itself as
soon as it isn't busy. The window says **Turning on Screen Recording…** meanwhile. It restarts once per
permission you allow; if that didn't turn it on, the window offers **Restart Auren** instead. The talk
keys start by themselves once Accessibility is allowed, also when you allow it in the prompt macOS
shows when Auren starts, with the setup window closed.

The window comes back from the menu bar icon (**Set up Auren…**, marked while something needs you), or
from opening Auren.app in Finder or Spotlight (or `auren open`) while setup isn't finished; once it is,
opening Auren.app shows the Auren window instead. `auren doctor` checks the same things in a terminal.

Until `auren setup` has run, `auren` only says to run it. Run it again any time: it keeps your
settings, keys and voice model.

Installed from a clone before? Run `$(brew --prefix)/bin/auren setup` once (the old `auren` in
`~/.local/bin` may come first on your PATH). It moves that `auren` and `auren-ctl` over to Homebrew's and
keeps your settings, keys and voice model.

### From a clone

The other way, and the one for working on Auren, is to run the installer from a clone. It does what
`auren setup` does, and links `auren` and `auren-ctl` into `~/.local/bin`:

```bash
git clone --branch v0.1.7 --depth 1 https://github.com/kvm404/Auren.git auren
cd auren && ./install-macos.sh
```

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
to resize it.

**The menu bar icon** says what Auren is doing, then has Talk, Fresh conversation and Stop (with their
keys), Speak replies, Send when I stop talking, and the Voice, Brain, Safety and Mascot menus. Below
them: **Open Auren…** (⌘,), **Phone…**, **Set up Auren…**, **Update to …** when a release is waiting,
Restart Auren and Quit Auren.

**The Auren window** has the pages of Omarchy's bar panel, in the look of System Settings:

| Page | |
|---|---|
| Home | The mascot (click it to talk), what Auren is doing, a Yes/No when it asks, what setup still needs, quick switches, the last exchange |
| Mascot | Auren or Veyra (each with its own voice, if you want), the speech bubble, the sleep animation, showing it, how faded it gets, its size and place |
| Voice | Spoken replies, the voice, speed, volume, the pause between sentences, a test; Send when I stop talking and after how long |
| Brain | Who answers (Auto, Claude, Codex), how hard it thinks, how much Auren asks before risky actions, today's numbers |
| Vocabulary | Names and words Auren should listen for |
| Phone | Pairing your phone ([below](#your-phone)) |
| Updates | The waiting release and what's new, Update now, Check for updates, the notification |

At the bottom of its sidebar: the settings file (`config.toml`, opened in your text editor), Auren's log
(in Console) and Restart Auren. The window and the menu change the same settings, so each shows what
the other changed.

`auren config set keys.talk cmd+shift+space` (and `keys.talk_fresh`) changes the keys; the daemon
picks them up without a restart.

## Your phone

The Android app works with Auren on a Mac as it does on Omarchy ([Phone remote](phone.md)).

1. Menu bar icon → **Phone…** (the Auren window's Phone page). Next to **Get the app**, click **Show
   code** and scan the QR code with your phone's camera, then open the download (Android asks once to
   allow installs from your browser).
2. Next to **Pair a phone**, click **Pair**, and in the app tap **Pair** and scan the QR code. The page
   shows how long the code still works; **Cancel**, or leaving the page, ends it at once.
3. The paired phones are listed below, with **Unpair**.

The phone reaches the Mac on the same Wi-Fi. To use it from anywhere, install
[Tailscale](https://tailscale.com/download/mac) on both and turn it on from its menu bar icon.

**The firewall.** If the macOS firewall is on (System Settings → Network → Firewall), macOS asks once,
when Auren first listens for a phone, whether **python3.x** (Auren's Python; it may say **Auren**) may
accept incoming network connections. Click **Allow**. If you clicked Deny, open Firewall → **Options**,
find it in the list and choose **Allow incoming connections**. With **Block all incoming connections**
on, no phone can connect. `auren doctor` says which of these applies.

## How the pieces map

| Omarchy | macOS |
|---|---|
| `pw-record` / `pw-cat` | PortAudio (`sounddevice`) |
| Hyprland bindings for Super+A | A Quartz event tap in the daemon (`keys.talk`) |
| `hyprctl` windows, focus, close | Quartz window list, Accessibility (AX) and `open -a` |
| Workspaces | Spaces, switched with Control+arrows (no API to move windows between Spaces) |
| `grim` + `ydotool`/`wtype` | `screencapture` + Quartz mouse and key events |
| `wl-copy` / `wl-paste` | NSPasteboard |
| `ip addr`, `/sys/class/net` (the phone's addresses) | `ifconfig` and `networksetup -listallhardwareports` |
| `ufw` (the phone's port) | The Application Firewall, which asks once per program |
| The bar panel (`omarchy-plugin/Panel.qml`) | The Auren window (`mascot/mac-window.qml`, its pieces in `mascot/mac/`) |
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
- macOS (from 15 or so) keeps other apps' folders from Auren and Terminal ("Operation not permitted").
  The browser tools still work once the extension connects, but browser profiles, bookmarks and
  downloads can't be read. To let Auren read them, allow it under Privacy & Security → Full Disk Access.
- Brightness keys need a built-in display (or `brew install brightness`).
- The mascot's bubble is a copy of the one in `mascot/shell.qml`: change both.
- Auren.app is signed ad hoc, not by a registered developer, so it can't be passed to another Mac as
  a download yet: each Mac builds its own with the installer.

## Updating

`auren update` checks for a new release and installs it in the background, as on Omarchy. Installed
with Homebrew, it waits until Auren is idle, stops it, runs `brew update` and `brew upgrade auren` (no
password needed), runs `auren setup` for the new version without asking anything, and starts Auren
again. If that setup fails, it sets up the version that was there again, from a copy `auren setup` keeps
in `~/.cache/auren/brew-rollback` (Homebrew removes the old version's own files). If Homebrew already
has a newer version (after a `brew upgrade` on its own), `auren update` sets it up without GitHub.

`brew upgrade` on its own (or as part of upgrading everything) updates Homebrew's copy only. Auren keeps
running the version it was set up with, and each `auren` command reminds you to run `auren setup` to finish.

Installed from a clone, Auren updates from its own clone in `~/.cache/auren/source` instead
([docs/releases.md](releases.md)).

## Removing

```bash
auren service uninstall
auren app uninstall
brew uninstall auren                          # from a clone instead: rm ~/.local/bin/auren ~/.local/bin/auren-ctl
rm -rf ~/.local/share/auren "$HOME/Library/Application Support/Auren"
rm -rf ~/.config/auren ~/.local/state/auren ~/.cache/auren      # your settings and history too
```

Then remove Auren from the lists in System Settings → Privacy & Security.
