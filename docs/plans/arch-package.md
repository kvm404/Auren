# Plan: Auren as an Arch package

Status: built in the PR that adds `packaging/arch/` (decisions below are the agreed ones).

## Goal

Install Auren with `yay -S auren` and one setup command, with no git clone. Omarchy is Arch, so only
an Arch package: no deb/rpm, and no Flatpak/Snap/AppImage (their sandboxes block evdev, screenshots,
`hyprctl`, user systemd units and starting Claude Code/Codex).

## Shape

Two halves.

**The package** (pacman owns these files)

| Path | What |
|---|---|
| `/usr/lib/auren/` | a private Python env with Auren and its dependencies |
| `/usr/bin/auren`, `/usr/bin/auren-ctl` | the commands (small wrappers into that env) |
| `/usr/lib/systemd/user/auren.service`, `auren-mascot.service` | the services, for every user |
| `/usr/share/auren/mascot/`, `/usr/share/auren/bar-plugin/` | the mascot QML and the Omarchy bar plugin |
| `depends` | `python`, `quickshell`, `pipewire`, `hyprland`; `optdepends`: `claude-code`, `codex` |

**`auren setup`** (per user, run once after installing; safe to run again)

1. Asks everything up front: talk keys, browser tools, AssemblyAI key, brain (Claude or Codex).
2. Adds the talk keys to `~/.config/hypr/bindings.lua` (backup first).
3. Links the bar plugin into `~/.config/omarchy/plugins/kvm404.auren`.
4. Writes `~/.config/auren/secrets.env` and the config.
5. Downloads the voice model (70 MB) into `~/.local/share/auren/models`.
6. Sets up the browser tools if chosen.
7. Enables and starts the units, then runs `auren doctor`.

## Decisions

Recommendations first; each is open until we agree.

1. **Package name and source.** `auren`, built from the GitHub release tarball. Add `auren-bin` only
   if build time becomes a complaint. Every dependency is in Arch's `extra` repo.
2. **Python dependencies: bundle a private env.** Arch has numpy, pillow, websockets, evdev and
   tomlkit, but its `python-mcp` is 1.29 (we need 2.3+) and `supertonic` isn't packaged at all. So the
   PKGBUILD builds a venv into `/usr/lib/auren` with `uv` from `uv.lock` (about 205 MB). Downside: a
   Python minor-version upgrade on Arch breaks the env until the package is rebuilt; bump `pkgrel`
   when that happens.
3. **Voice model: downloaded by `auren setup`**, not packaged. It's 70 MB, per user, and
   `auren voice-model` already does it.
4. **Setup: one command, `auren setup`**: the per-user half of today's `install.sh`, ported to
   Python. Same questions and flags (`--keys=…`, `--browser` / `--no-browser`) so the coding-agent
   install prompt still works without questions. pacman's post-install message says to run it.
5. **Updates: pacman owns them.** In a package install, `auren update` only checks and says
   "run `yay -Syu auren`"; the bar's button opens a terminal running that. The self-updater stays for
   `install.sh` installs. After an upgrade, the post-upgrade message (and `auren doctor`) says to
   restart the units, as install.sh does now.
6. **Moving from an install.sh install.** `auren setup` finds `~/.local/share/auren/venv` and the old
   units in `~/.config/systemd/user`, removes them (user units would shadow the package's), and keeps
   config, secrets, models and state.
7. **Maintenance: PKGBUILD in this repo** (`packaging/arch/`); the release workflow pushes it to the
   AUR with the new version and checksum. Omarchy's own package repo is a later goal.
8. **Uninstall.** `pacman -R` leaves user files alone (pacman can't touch home folders). Run
   `auren setup --remove` first: it removes the bindings lines and the bar plugin link and disables
   the units; config and secrets stay unless `--purge`.

## Code changes

- Paths: Auren finds its mascot, bar plugin and units from where it's installed, not the repo
  (`/usr/share/auren` in a package, `~/.local/share/auren` otherwise). One place decides.
- `auren setup` and `auren setup --remove` in Python. `install.sh` keeps the build and copy steps and
  calls `auren setup` for the rest, so both paths share one setup.
- `auren update` knows whether it's a package install (a field in `install.json`) and defers to
  pacman.
- `packaging/arch/PKGBUILD`, `auren.install` (post-install and post-upgrade messages), release step.
- Tests: `auren setup` in an isolated HOME with every `XDG_*` var overridden; `makepkg` in a clean
  container in CI; `namcap` on the result.
- Docs: the README install section becomes `yay -S auren && auren setup`; install.md keeps the
  from-source path for development.

## Order

1. One source of install paths, and `auren setup` ported from install.sh (install.sh calls it).
   Ships on its own: nothing changes for users yet.
2. PKGBUILD and `auren.install`; build and install it locally in a clean environment.
3. `auren update` and the bar button for package installs; migration from install.sh installs.
4. The release workflow pushes to the AUR; the README switches to `yay -S auren`.

## From Voisu

Voisu (`voisu-bin` on the AUR) ships the same way: system files only, and its pacman scriptlet only
prints instructions. Two things to copy:

- **Three messages in `auren.install`.** `post_install`: run `auren`. `post_upgrade`: restart the
  units (`systemctl --user try-restart auren auren-mascot`). `pre_remove`/`post_remove`: run
  `auren setup --remove` *before* `pacman -R`, because once pacman deletes the unit files,
  `systemctl --user disable` can't find them and leaves dangling links in
  `graphical-session.target.wants`.
- **No `ConditionEnvironment=WAYLAND_DISPLAY` in the units.** systemd checks a condition once and
  never retries it, so if the session's variables arrive late at login, Auren is skipped until the
  next login. Voisu dropped the condition and has the daemon wait for the display itself (exit 75
  and restart, if it never comes, exit 78 with `RestartPreventExitStatus=78`). Worth doing in
  step 1, since it affects install.sh installs too.

## Answered

- The AUR account and the `auren` package belong to kvm404.
- `auren setup` runs by itself the first time Auren starts, if setup hasn't been done. It can still
  be run by hand, again, at any time.

- install.sh becomes development-only. The next release still self-updates install.sh installs, then
  shows a one-time notice to switch with `yay -S auren`; `auren setup` migrates the old install. After
  that the self-updater is dropped, and the bar's update button sends you to `yay -Syu`.
- No new app and no launcher entry: the package ships the existing CLI, services, mascot and bar
  plugin. pacman's post-install message says to run `auren`, and the first run starts setup.
- Setup's questions use gum (already on every Omarchy machine, and what Omarchy's own scripts use),
  not a full-screen TUI. Without a terminal, or with `--yes`, it asks nothing.
