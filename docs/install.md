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

Follow the [README quick start](../README.md#quick-start). A normal install is a copy, while
`./install.sh --dev` links the runtime and UI to your checkout.

## For coding agents

If a user asked you to install Auren, work through these steps and tell them what you're doing as you go.

1. Check the machine: Omarchy with Hyprland 0.56 or newer and Lua configuration, PipeWire, and a
   microphone. Read any local agent instructions for Omarchy configuration. If a requirement is
   missing, stop and tell the user.
2. Check for `git`, `uv`, `qs` (Quickshell), `wtype`, `wl-clipboard` and `grim`. Show the user what's
   missing and ask before installing it with pacman.
3. Find Claude Code and Codex. Run a short test prompt to make sure at least one is signed in. If
   neither works, help the user sign in.
4. Use the latest published GitHub release, excluding drafts and prereleases, and check out its exact
   tag in a temporary folder. If there is no release, tell the user instead of installing `main`.
   Run `./install.sh` (a normal install, not `--dev`). It downloads a 380 MB voice model.
5. `install.sh` creates `~/.config/auren/secrets.env` with mode 600. Give the user one command to add
   `ASSEMBLYAI_API_KEY=...` themselves. Never ask them to paste the key into the conversation, and
   never print it.
6. Back up `~/.config/hypr/bindings.lua` and add the lines `install.sh` printed. They unbind Omarchy's
   Super+A (Select all) and Super+Shift+A (ChatGPT). Ask the user first, and use the printed
   Super+Alt+A lines if they want to keep Omarchy's. Don't overwrite their other shortcuts.
7. Ask about the optional integrations one at a time: herdr for coding agents, Open Browser Use for
   browser tasks, and `/dev/uinput` access for mouse control and Excalidraw. Set up the ones the user
   wants and turn the others off with `auren config set integrations.<name> false`.
8. If only Codex is available, run `auren config set brain.mode codex`. Keep the default `ask`
   approval level.
9. Run `auren doctor` and fix what it reports. Restart Auren while it is idle, then check that it
   speaks with `auren-ctl say "Hi, I'm Auren"`.
10. Finish with a short summary: what you installed, what changed in the user's config, anything they
    still need to do, and three things to say after pressing Super+A.

## Optional integrations

- **herdr:** install it and open a session for coding-agent tools. Without it, shell jobs run in the
  background with output under `~/.local/state/auren/jobs/`.
- **Browser:** install Open Browser Use and its extension in a supported Chromium browser. The
  `open-browser-use` command must be on the service's PATH. The default browser is detected;
  `auren config set browser.name brave`, for example, overrides it. Auren uses its own browser tabs.
  Browser tools cannot currently claim standalone web-app windows; those use screen control.
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
omarchy plugin disable krvm.auren
rm -f ~/.config/systemd/user/auren.service ~/.config/systemd/user/auren-mascot.service
rm -f ~/.config/omarchy/plugins/krvm.auren ~/.local/bin/auren ~/.local/bin/auren-ctl
rm -rf ~/.local/share/auren
systemctl --user daemon-reload
```

Remove the bindings you added to Hyprland. Your settings, API key, history, logs, and cached profile
remain. See [Privacy](privacy.md#local-storage-and-removal) to delete them. Substitute your XDG paths
if you use custom folders.
