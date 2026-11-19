<h1 align="center">Auren</h1>

<p align="center">
  A voice assistant for Omarchy, and for macOS in preview.<br>
  Talk to it, and it opens apps, plays music, messages friends and runs coding agents in herdr.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#using-it">Using it</a> ·
  <a href="docs/README.md">Docs</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

<p align="center">
  <img src="docs/images/demo.gif" alt="Auren opening apps, playing music, sending a message, drawing in Excalidraw and starting Codex" width="800">
</p>

Press **Super+A** and say what you want. A tiny robot on your desktop shows what it's doing and
answers out loud. When an app has no command for something, Auren looks at the screen and uses the
mouse and keyboard.

## Install

Auren runs on [Omarchy](https://omarchy.org) and, as a preview, on Apple Silicon Macs. On both you
need Claude Code or Codex, signed in, and an [AssemblyAI](https://www.assemblyai.com/) API key for
speech to text.

### Omarchy

Auren is in the AUR:

```bash
yay -S auren
auren
```

The first `auren` sets it up for you. It asks which keys start Auren (it adds them to
`~/.config/hypr/bindings.lua` after a backup), whether to set up the browser tools so Auren can work
in your own logged-in browser, and for your AssemblyAI key. Then it adds the bar plugin, downloads
the voice model, starts Auren and runs `auren doctor`. Run `auren setup` again any time.

Updates come with your system updates (`yay`), or from the bar panel.

Installed Auren before it was a package, with `./install.sh`? Run `yay -S auren`, then
`/usr/bin/auren setup` once. It moves your install over and keeps your settings, keys and voice model.

To let a coding agent do it, paste this into Claude Code or Codex:

```text
Install Auren from https://github.com/kvm404/Auren on this Omarchy machine.
Follow "For coding agents" in its docs/install.md.
Ask all your questions at the start, then install everything in one go.
```

The [install guide](docs/install.md) covers optional extras, troubleshooting, removal, and installing
from a checkout.

### macOS (preview)

You need an Apple Silicon Mac on macOS 14 or newer, and [uv](https://docs.astral.sh/uv/)
(`brew install uv`).

```bash
git clone --branch v0.1.6 --depth 1 https://github.com/kvm404/Auren.git auren
cd auren && ./install-macos.sh
```

It installs Auren.app and opens its setup window, which walks through the permissions, the AssemblyAI
key and the brain. Then press **Control+Option+A** to talk. `auren update` installs new versions.
[Auren on macOS](docs/macos.md) has the details and what doesn't work yet.

## Using it

| | |
|---|---|
| **Super+A** (Mac: Control+Option+A) | Start talking. Press again to send, or while it works to stop it. |
| **Super+Shift+A** (Mac: Control+Option+Shift+A) | Start a fresh conversation. |
| **Mascot** | Click to talk. Drag to move it, scroll to resize it. |
| **Bar icon** (Mac: menu bar icon) | Change the mascot, voice, brain and vocabulary, and install updates. |

Things to try:

- "Open Obsidian on workspace 2."
- "Play some lofi on Spotify."
- "Draw how login works in Excalidraw."
- "Find the cheapest flight to Goa next Friday." (with the browser tools)
- "Ask Codex to fix the failing tests in my project."

From your phone: the Android app in `android/` is a remote over Tailscale (talk, type, answer, stop, and
get links and files from Auren). Pair it with `auren phone pair`; see [Phone remote](docs/phone.md).

If something doesn't work, run `auren doctor`.

## Under the hood

| | |
|---|---|
| **Brain** | Your signed-in Claude Code on Claude Haiku, with Codex as backup. No extra API key. |
| **Ears** (speech to text) | AssemblyAI, only while you're talking. |
| **Voice** (text to speech) | Gradium's Phonon, running on your own CPU, in four voices. |
| **Coding agents** | herdr (optional) |
| **Browser** | Open Browser Use (optional) |

Haiku is quick because Auren's tools do the heavy lifting. A simple answer comes back in about two
seconds.

## Safety and privacy

Auren asks before anything risky, and always asks before publishing, buying or changing the system, except at Full access (`never`), which asks about nothing.
These checks catch mistakes, but they are not a sandbox.

Your voice goes to AssemblyAI, and your request goes to Claude or Codex. The voice, your history and
logs stay on your machine. Read [Safety](docs/safety.md) and [Privacy](docs/privacy.md) for the details.

## Contributing

Auren is free and open source. Bug reports, ideas and pull requests are welcome. Start with
[Contributing](CONTRIBUTING.md) and the [roadmap](docs/roadmap.md). Releases are listed in the
[changelog](CHANGELOG.md).

---

<p align="center">MIT licensed</p>
