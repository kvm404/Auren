# Install and troubleshoot

## Requirements

Auren requires Omarchy with Hyprland 0.56 or newer, Lua configuration, Wayland, PipeWire, and the
Omarchy shell. Older Hyprland versions do not provide the Lua dispatchers used by Auren.
The installer needs `git`, `uv`, `qs`, `hyprctl`, `pw-record`, and `pw-cat`. Screen capture needs `grim`; typing needs
`wtype`, and clipboard pasting needs `wl-copy` and `wl-paste` from `wl-clipboard`.

Install and sign in to Claude Code or Codex. Auren uses your locally installed CLI and its selected
model. It does not yet have an API-key model backend. Microphone input requires an AssemblyAI key.
The local Supertonic voice model downloads about 380 MB during installation; a failed download leaves
Auren able to show text replies. Retry with `auren voice-model`.

Follow the [README installation steps](../README.md#install). A normal install is a copy, while
`./install.sh --dev` links the runtime and UI to your checkout.

The installer enables `auren.service` and `auren-mascot.service` for your graphical session and
registers the `kvm404.auren` bar plugin. It prints Hyprland bindings instead of editing your config.
It does not install system packages or grant device access.

## For coding agents

If a user asked you to install Auren, check first, ask once, then do the rest without stopping.

### 1. Check, without changing anything

- Omarchy with Hyprland 0.56 or newer and Lua configuration, PipeWire, and a microphone. If one is
  missing, stop and tell the user. Read any local agent instructions for Omarchy configuration.
- Which of `git`, `uv`, `qs` (Quickshell), `wtype`, `wl-clipboard` and `grim` are missing.
- Claude Code and Codex: run a short test prompt to see which one is signed in.
- What is already bound to Super+A and Super+Shift+A in `~/.config/hypr/bindings.lua`.
- Which optional integrations are available: herdr for coding agents, and `/dev/uinput` write access
  for mouse control and Excalidraw (`test -w /dev/uinput`). Browser tools need a Chromium browser
  (Chrome, Chromium, Brave, Helium, Edge or Vivaldi; not a Flatpak). Note the default browser
  (`omarchy-cmd-default-browser`, else `xdg-settings get default-web-browser`). If Auren is already
  installed, `auren browser` shows whether they're set up.
- The latest published GitHub release, excluding drafts and prereleases. If there is none, tell the
  user instead of installing `main`.

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

1. Install the missing packages with pacman. If you can't run `sudo` yourself, give the user one
   command for all of them.
2. Check out the release's exact tag in a temporary folder and run `./install.sh --browser` (or
   `--no-browser` if the user didn't want the browser tools); a normal install, not `--dev`. It
   downloads a 380 MB voice model. With `--browser`, tell the user just before it gets there to click
   Add on the Web Store page that opens; it waits up to three minutes. If no window could open, run
   `auren browser setup --yes` afterwards instead.
3. Write `ASSEMBLYAI_API_KEY=...` to `~/.config/auren/secrets.env` (mode 600) without printing it, or
   tell the user the exact file and line to add.
4. Back up `~/.config/hypr/bindings.lua`, then add the binding lines `install.sh` printed for the
   chosen keys. Don't change the user's other shortcuts.
5. Turn off the other extras the user didn't want with `auren config set integrations.<name> false`
   (`herdr` or `screen`). If only Codex works, run `auren config set brain.mode codex`.
   Keep the default `ask` approval level.
6. Run `auren doctor` and fix what it reports. Restart Auren, then check that it speaks with
   `auren-ctl say "Hi, I'm Auren"`.

### 4. Finish

Give a short summary: what you installed, what changed in the user's config, anything they still need
to do, and three things to say after pressing the talk key.

## Optional integrations

- **herdr:** install it and open a session for coding-agent tools. Without it, shell jobs run in the
  background with output under `~/.local/state/auren/jobs/`.
- **Browser:** `auren browser setup` (or `./install.sh --browser`) sets up Open Browser Use in your
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
(Omarchy 4.5 puts Select all on Super+A; the lines `install.sh` prints already unbind it).
After changing the session environment, restart Auren while idle.

Use `auren config set brain.mode codex` when Codex is your only installed brain. The send judge uses
Claude separately and asks for confirmation if Claude is unavailable.

## Remove Auren

Stop and disable the services, disable the bar plugin, then remove the installed files:

```bash
systemctl --user disable --now auren auren-mascot
omarchy plugin disable kvm404.auren
rm -f ~/.config/systemd/user/auren.service ~/.config/systemd/user/auren-mascot.service
rm -f ~/.config/omarchy/plugins/kvm404.auren ~/.local/bin/auren ~/.local/bin/auren-ctl
rm -rf ~/.local/share/auren
systemctl --user daemon-reload
```

If Auren set up the browser tools, also remove the manifests it wrote: the
`NativeMessagingHosts/com.ifuryst.open_browser_use.extension.json` files under `~/.config` whose
`path` points into `~/.local/share/auren/obu`, then remove the extension in your browser if you don't
use it otherwise.

Remove the bindings you added to Hyprland. Your settings, API key, history, logs, and cached profile
remain. See [Privacy](privacy.md#local-storage-and-removal) to delete them. Substitute your XDG paths
if you use custom folders.
