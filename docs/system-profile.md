# System profile

What Auren knows about this laptop, and where each fact comes from. Code: `src/auren/profile/`.

```bash
uv run auren-profile                        # the text the brain reads (~1.1k tokens)
uv run auren-profile lookup keys obsidian   # search a long list
uv run auren-profile --json                 # everything
uv run auren-profile --refresh              # rebuild the cache now
```

## How it works

- **Static facts** are cached in `~/.cache/auren/profile-static.json`. They are rebuilt automatically when a source changes: launcher folders, `~/.config/hypr`, `~/.bashrc`, the Omarchy defaults, the Obsidian config, the project folders, the browser's bookmarks, `config.toml`, or the Omarchy version. They are also rebuilt after 6 hours.
- **Live facts** are read on every request: windows, herdr panes, volume, battery, memory and time. The whole snapshot takes ~70 ms.
- **The brain gets a compact summary** (`context()`) with every request. Long lists stay out of the prompt and are searched on demand with `lookup(topic, words)`.
- **Secrets are masked** in every output (`redact.py`, tested):
  - `NAME=value` where the name looks secret (KEY, TOKEN, PASSWORD…);
  - credentials in URLs;
  - well-known token shapes;
  - `--password` / `--token` flags.

  The user's `.bashrc` has three such secrets, and none of them reach the profile.

## Sources

| Fact | Source |
|---|---|
| Omarchy version, all 367 commands (route, args, summary, needs sudo) | `omarchy version`, `omarchy commands --json` |
| Key bindings (218) as "keys → action" | `omarchy-menu-keybindings --print`. This is a hidden flag; `hyprctl binds` alone shows Lua binds as `__lua` with no action, and keycode binds with no key name |
| Apps, web apps, terminal apps | `.desktop` files in `/usr/share/applications`, Flatpak exports and `~/.local/share/applications` (user files win). Entries marked NoDisplay or Hidden are skipped |
| Web apps | launchers whose Exec is `omarchy-launch-webapp <url>` (created by `omarchy webapp install`). They open in the default browser's app mode (`--app=<url>`; Brave here, Chromium otherwise) |
| Default apps | `xdg-settings` (browser), `xdg-terminal-exec --print-id` (terminal), `~/.local/state/omarchy/defaults/editor` (falls back to nvim), `~/.config/omarchy/defaults/agent`, `xdg-mime` per file type |
| Aliases and functions | `bash -ic alias`, `bash -ic 'declare -F'`. Omarchy's own (`~/.local/share/omarchy/default/bash/*`) plus the user's `.bashrc` |
| Windows, workspaces, monitors | `hyprctl clients/activewindow/monitors -j` |
| herdr workspaces, tabs, panes, agents | `herdr workspace/tab/pane/agent list` (JSON) |
| Projects | the folders in `projects.folders`, or the usual ones that exist (`~/Projects`, `~/Code`, `~/src`, …); newest first, with the first README line |
| Obsidian vaults | `~/.config/obsidian/obsidian.json` (here: `~/Obsidian`) |
| Battery, volume, memory | `/sys/class/power_supply`, `wpctl get-volume`, `/proc/meminfo` |
| User services | `systemctl --user` (running, minus desktop plumbing) |

## Facts about this setup worth knowing

- **Omarchy 4.0.4.** Hyprland is configured in **Lua** (`~/.config/hypr/*.lua`), with defaults in `/usr/share/omarchy/default/hypr/`.
- **Defaults:** browser Brave, terminal foot, editor Neovim, Omarchy's default coding agent **Codex**, PDFs in Document Viewer (Evince), Markdown in Typora.
- **herdr runs in a foot terminal on the scratchpad workspace.** herdr workspaces in use: "TDS", "Obsidian" (panes in `~/Obsidian`) and "PROJECTS".
- **Not installed:** Firefox, Spotify (`omarchy launch spotify` installs it), Slack, Telegram, Signal, VLC, GIMP, Thunderbird. Discord exists only as a web app.
- **Useful Omarchy commands for the hands:**
  - `omarchy launch or focus <pattern> <cmd>`, `omarchy launch webapp <url>`, `omarchy hyprland focus app <name>`;
  - `omarchy capture screenshot [smart|region|windows|fullscreen] [slurp|copy|save]`, `omarchy capture text` (OCR);
  - `omarchy audio output volume <raise|lower|mute-toggle|+N|N>`, `omarchy brightness display`;
  - `omarchy reminder <minutes> <text>`, `omarchy notification send`, `omarchy toggle nightlight|idle|…`;
  - `omarchy launch terminal herdr`;
  - `omarchy agent prompt <prompt>` (starts the default coding agent with a prompt).
- **Media keys** go through Omarchy's Lua binds. `playerctl` isn't installed.
