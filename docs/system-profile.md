# System profile

What Auren knows about the machine, and where each fact comes from. Code: `src/auren/profile/`.

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

  Redaction matches known patterns, not every possible secret. See [Privacy](privacy.md).

## Sources

| Fact | Source |
|---|---|
| Omarchy version, available commands (route, args, summary, needs sudo) | `omarchy version`, `omarchy commands --json` |
| Key bindings as "keys → action" | `omarchy-menu-keybindings --print`. This is a hidden flag; `hyprctl binds` alone shows Lua binds as `__lua` with no action, and keycode binds with no key name |
| Apps, web apps, terminal apps | `.desktop` files in `/usr/share/applications`, Flatpak exports and `~/.local/share/applications` (user files win). Entries marked NoDisplay or Hidden are skipped |
| Web apps | launchers whose Exec is `omarchy-launch-webapp <url>` (created by `omarchy webapp install`). They open in the default browser's app mode (`--app=<url>`) |
| Default apps | `xdg-settings` (browser), `xdg-terminal-exec --print-id` (terminal), `~/.local/state/omarchy/defaults/editor` (falls back to nvim), `~/.config/omarchy/defaults/agent`, `xdg-mime` per file type |
| Aliases and functions | `bash -ic alias`, `bash -ic 'declare -F'`. Omarchy's own (`~/.local/share/omarchy/default/bash/*`) plus the user's `.bashrc` |
| Windows, workspaces, monitors | `hyprctl clients/activewindow/monitors -j` |
| herdr workspaces, tabs, panes, agents | `herdr workspace/tab/pane/agent list` (JSON) |
| Projects | the folders in `projects.folders`, or the usual ones that exist (`~/Projects`, `~/Code`, `~/src`, …); newest first, with the first README line |
| Obsidian vaults | `~/.config/obsidian/obsidian.json` |
| Battery, volume, memory | `/sys/class/power_supply`, `wpctl get-volume`, `/proc/meminfo` |
| User services | `systemctl --user` (running, minus desktop plumbing) |

## Portability and limitations

Browser, terminal, editor, coding agent, projects, and installed apps are detected on each machine.
Available sources and application choices vary by installation. Projects and browser behavior can
be overridden in `config.toml`.

Collector failures return an error for that source instead of preventing every other collector from
running. The profile can include personal paths and window titles even after secret redaction.
Do not publish the full profile without reviewing it.
