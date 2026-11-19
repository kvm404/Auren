# Install and troubleshoot

This guide is for Omarchy. On a Mac, see [Auren on macOS](macos.md).

## Requirements

Auren requires Omarchy with Hyprland 0.56 or newer, Lua configuration, Wayland, PipeWire, and the
Omarchy shell. Older Hyprland versions do not provide the Lua dispatchers used by Auren.
The `auren` package installs everything else it needs: Quickshell, PipeWire's tools, `grim` for screen
capture, `wtype` for typing, and `wl-clipboard` for pasting.

Install and sign in to Claude Code or Codex. Auren uses your locally installed CLI and its selected
model. It does not yet have an API-key model backend. Microphone input requires an AssemblyAI key.
The local Supertonic voice model downloads about 380 MB during setup; a failed download leaves
Auren able to show text replies. Retry with `auren voice-model`.

## The package and `auren setup`

`yay -S auren` installs the program for every user: its own Python environment in `/usr/lib/auren`
(Auren's Python dependencies aren't all packaged for Arch), the `auren` and `auren-ctl` commands,
the mascot and bar plugin in `/usr/share/auren`, and the two services in `/usr/lib/systemd/user`.
pacman never writes into your home folder or starts services for you, so the rest is `auren setup`,
which the first `auren` runs:

- It asks every question first: the talk keys, the browser tools, your AssemblyAI key (if Auren has
  none), and, if both Claude Code and Codex are installed, which one answers. In a terminal the
  questions use gum, like Omarchy's own scripts. Ctrl+C or Esc during a question stops setup before
  anything changes.
- It adds the talk keys to `~/.config/hypr/bindings.lua`, after saving a copy as
  `bindings.lua.bak.<time>`; it doesn't touch the rest of your Hyprland config.
- It links `~/.local/share/auren/{bin,mascot,bar-plugin}` to the package's files, so key bindings,
  the mascot and the bar plugin use the same paths however Auren was installed, and registers the
  `kvm404.auren` bar plugin.
- It saves the key in `~/.config/auren/secrets.env` (mode 600), downloads the voice model, sets up
  the browser tools if you said yes, enables and starts `auren.service` and `auren-mascot.service`,
  and runs `auren doctor`.

Running it again is safe: what's done stays done. Flags answer the questions without asking:
`--keys=super`, `--keys=alt` or `--no-keys` (print the lines instead), `--browser` or `--no-browser`,
`--brain=auto|claude|codex`. `--yes` asks nothing, and leaves for later whatever no flag answered.
Without a terminal it asks nothing too. It does not grant device access.

If Auren was installed with `install.sh` before, run `/usr/bin/auren setup` once (until then the old
`auren` in `~/.local/bin` comes first in PATH). Setup replaces that copy: it stops and removes the
old services, `~/.local/share/auren/venv`, its UI files, the `auren` links in `~/.local/bin` and the
self-updater's clone. Your settings, API key, talk keys, voice model, history and browser tools stay.

Updates come with `yay -Syu`; restart Auren afterwards (`systemctl --user try-restart auren
auren-mascot`). The bar panel's Update button does it for you: it opens a terminal that waits until
Auren is idle, stops it, runs `yay -S --needed auren`, and starts it again. When Arch moves to a new
Python version, rebuild Auren: `yay -S --rebuild auren` (the `auren` command says so).

### From a checkout

`install.sh` installs a checkout for your user, for working on Auren: the program goes into
`~/.local/share/auren` (`--dev` links the Python code and the UI to the checkout instead of copying
them), then it runs the same `auren setup`, with the same flags. It needs `git`, `uv`, `qs`,
`hyprctl`, `pw-record`, and `pw-cat`. See [CONTRIBUTING](../CONTRIBUTING.md).

## For coding agents

If a user asked you to install Auren, check first, ask once, then do the rest without stopping.

### 1. Check, without changing anything

- Omarchy with Hyprland 0.56 or newer and Lua configuration, PipeWire, and a microphone. If one is
  missing, stop and tell the user. Read any local agent instructions for Omarchy configuration.
- Whether `yay` (or `paru`) is there to install AUR packages.
- Claude Code and Codex: run a short test prompt to see which one is signed in.
- What is already bound to Super+A and Super+Shift+A in `~/.config/hypr/bindings.lua`.
- Which optional integrations are available: herdr for coding agents, and `/dev/uinput` write access
  for mouse control and Excalidraw (`test -w /dev/uinput`). Browser tools need a Chromium browser
  (Chrome, Chromium, Brave, Helium, Edge or Vivaldi; not a Flatpak). Note the default browser
  (`omarchy-cmd-default-browser`, else `xdg-settings get default-web-browser`). If Auren is already
  installed, `auren browser` shows whether they're set up.
- That the AUR has the package: `yay -Si auren`. If it doesn't, stop and tell the user.

### 2. Ask once

Send one message: what you will install and change, then at most three questions, each with your
recommended answer, so the user can reply "go".

1. **AssemblyAI key:** paste it here, or add it themselves? Recommend pasting it. Never print it back.
2. **Talk keys:** take over Super+A and Super+Shift+A from Omarchy (Select all and ChatGPT), or use
   Super+Alt+A and Super+Ctrl+Alt+A? Recommend Super+A. Skip this if the user already has their own
   binding on those keys, and use the alternative.
3. **Optional extras:** list only the available ones and recommend turning them all on. Skip the
   question if none are available. For the **browser tools**, name the browser they'll use (the
   default one) and say what it means: Auren downloads Open Browser Use and opens its extension's
   Web Store page there, and the user clicks Add while the install waits. Skip them if no Chromium
   browser is installed, or if `auren browser` says they already work.

If neither Claude Code nor Codex is signed in, help the user sign in before you ask. Don't ask
anything else unless something fails.

### 3. Install

1. Install the package: `yay -S --noconfirm auren`. It needs `sudo`; if you can't run it yourself,
   give the user that one command and wait.
2. Write `ASSEMBLYAI_API_KEY=...` to `~/.config/auren/secrets.env` (mode 600) without printing it, or
   tell the user the exact file and line to add.
3. Run `auren setup --yes --browser` (or `--no-browser` if the user didn't want the browser tools)
   with `--keys=super` or `--keys=alt` for the chosen talk keys, and `--brain=codex` if only Codex
   works. It adds the keys to `bindings.lua` after a backup, and leaves them alone if Auren is already
   bound. It downloads a 380 MB voice model. With `--browser`, tell the user just before it gets
   there to click Add on the Web Store page that opens; it waits up to three minutes. If no window
   could open, run `auren browser setup --yes` afterwards instead.
4. Turn off the other extras the user didn't want with `auren config set integrations.<name> false`
   (`herdr` or `screen`). Keep the default `ask` approval level.
5. Run `auren doctor` and fix what it reports. Restart Auren, then check that it speaks with
   `auren-ctl say "Hi, I'm Auren"`.

### 4. Finish

Give a short summary: what you installed, what changed in the user's config, anything they still need
to do, and three things to say after pressing the talk key.

## Optional integrations

- **herdr:** install it and open a session for coding-agent tools. Without it, shell jobs run in the
  background with output under `~/.local/state/auren/jobs/`.
- **Browser:** `auren browser setup` (or `auren setup --browser`) sets up Open Browser Use in your
  default browser, or in the one you name (`auren browser setup brave`): it downloads Open Browser
  Use into `~/.local/share/auren/obu/`, registers it with the browser, and opens the extension's
  Web Store page, where you click Add. You can also just ask Auren to set it up. It works in any
  Chromium browser (Chrome, Chromium, Brave, Helium, Edge, Vivaldi; not Flatpaks) and any profile
  with the extension, including an Open Browser Use you set up yourself. `auren browser` shows
  what's connected and which browser and profile Auren uses; `auren browser use helium School`
  pins one. Auren works in its own tabs. Browser tools cannot currently claim standalone web-app
  windows; those use screen control.
- **Mouse control:** your user needs write access to `/dev/uinput` through your system's device
  permissions. Check with `test -w /dev/uinput`. Without access, mouse and Excalidraw tools are hidden;
  screenshots and keyboard tools can still be offered. Device access must be configured separately.

Turn off an integration explicitly with `auren config set integrations.browser false`, replacing
`browser` with `herdr` or `screen` as needed. `auto`, the default, offers an integration when detected.

## Diagnostics

```bash
auren doctor
systemctl --user status auren auren-mascot
journalctl --user -u auren -n 100
journalctl --user -u auren-mascot -n 100
```

If voice input fails, check the AssemblyAI key, network, and default PipeWire microphone.
If the brain fails, run the chosen CLI in a terminal to check sign-in, model access, and usage limits.
Automatic fallback handles Claude usage limits before an action runs; it is not a retry for every error.
If a command works in a terminal but not in Auren, `auren doctor` checks the systemd manager's PATH too.
If pressing the talk key also does something else, `auren doctor` names the other binding on that key.
Hyprland runs every binding on a key, so unbind the other one with `hl.unbind` above Auren's line
(Omarchy 4.5 puts Select all on Super+A; the lines `auren setup` adds already unbind it).
After changing the session environment, restart Auren while idle.

Use `auren config set brain.mode codex` when Codex is your only installed brain. The send judge uses
Claude separately and asks for confirmation if Claude is unavailable.

## Remove Auren

```bash
auren setup --remove
sudo pacman -R auren
```

`auren setup --remove` stops and turns off the services, removes the bar plugin, takes Auren's lines
out of `bindings.lua` (after a backup), and removes the browser host Auren set up and the links in
`~/.local/share/auren`. Remove the Open Browser Use extension in your browser if you don't use it
otherwise. Your settings, API key, history, logs and voice model stay; `auren setup --remove --purge`
deletes them too (see [Privacy](privacy.md#local-storage-and-removal)).

Run it before `pacman -R`: once the package is gone, the `auren` command is too. If you removed the
package first, pacman prints the commands that do the same by hand.

An install made with `install.sh` is removed the same way, without the `pacman` step: `auren setup
--remove` also deletes the program in `~/.local/share/auren` and its services.
