# Bar plugin

`krvm.auren`, an Omarchy shell bar widget. Code: `omarchy-plugin/` (`manifest.json`, `Panel.qml`,
`MiniClawd.qml`), linked as `~/.config/omarchy/plugins/krvm.auren`. Enabled with
`omarchy plugin enable krvm.auren --before krvm.agents` (a `shell.json` backup was made first). Saved edits
reload by themselves.

## Bar icon

A 16x10 pixel Clawd (the logo's blocks at one pixel each):
- **idle:** bar colour;
- **active** (listening, thinking, working, speaking, waiting for a yes/no): Claude orange and bobbing;
  arms up while listening;
- **error:** red;
- **off:** dim, eyes shut.

Hover shows the status. **Click** opens the panel; **right-click** starts a fresh conversation.

## Panel

Built with Omarchy's own `qs.Ui` parts (`Panel`, `BarIconButton`, `KeyboardPanel`, `Toggle`, `Button`,
`PanelSectionHeader`), so it follows the theme and supports the keyboard (arrows, Enter, Esc, Tab to the
next panel).

| Part | What it does | How |
|---|---|---|
| Hero | live Clawd (the mascot's own `Clawd.qml`, loaded only while the panel is open), status, brain, voice | **click Clawd to talk** (again to send), right-click to poke; starts Auren when it's off, stops it talking |
| Last exchange | what you said and Auren's reply | `~/.local/state/auren/history.jsonl` |
| Auren | on/off | `systemctl --user start/stop auren` |
| Voice replies | off = answers only in the mascot's bubble | `auren-ctl voice on/off` |
| Mascot | on/off (off stops the process, freeing ~100 MB) | `systemctl --user start/stop auren-mascot` |
| Sleep animation | smooth breathing while asleep (~6% CPU) or still | writes `sleepAnimation` in `~/.config/auren/mascot.json` (the mascot reloads it) |
| Voice | F2 (Auren's), M3, F1, M1 (the shortlist from decision 0001); a click plays a sample | `auren-ctl voice-name <name>` |
| Brain | Auto (Claude, Codex on a limit) / Claude only / Codex only | `auren-ctl brain auto/claude/codex` |
| Actions | Fresh (forget the conversation), Stop, Restart, Log | `auren-ctl forget/cancel`, `systemctl --user restart auren`, the log in a terminal |
| Today | requests today and average time to answer | history log |

The daemon keeps voice and brain in `~/.config/auren/settings.json` and puts them in `state.json`
(`voice`, `mode`), so the panel, the mascot and restarts all agree.

From a script or key binding: `omarchy-shell krvm.auren toggle` (or `open`, `close`).

## Notes

- **Code changes need `omarchy restart shell`.** The plugin folder is a link into the repo, and the shell's
  change watcher doesn't see edits through it (`rescanPlugins` doesn't reload code either).
- The plugin bar API has `run(command)` but no `shellQuote` (the bar README lists it). The plugin starts
  its commands with `Quickshell.execDetached([...])` instead. The first version called `shellQuote`, so every
  auren-ctl action in the panel failed until this was fixed.

- Quickshell's file watch does see the daemon's write-then-rename updates of `state.json` (tested), so the
  plugin and the mascot only re-read it every 10 s as a safety net. They used to re-read it every second,
  which caused "dropped operation" warnings.
- Dictation will get its switch here when it's built.
