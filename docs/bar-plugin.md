# Bar plugin

`krvm.auren`, an Omarchy shell bar widget. Code: `omarchy-plugin/` (`manifest.json`, `Panel.qml`, and its
parts `MiniMascot.qml`, `Choice.qml`, `SliderRow.qml`), linked as `~/.config/omarchy/plugins/krvm.auren`.
Enabled with `omarchy plugin enable krvm.auren --before krvm.agents` (a `shell.json` backup was made first).

## Bar icon

The chosen mascot in pixels (Auren 16x12, Veyra 16x11, one pixel per unit):
- **idle:** bar colour;
- **active** (listening, thinking, working, speaking, waiting for a yes/no): the mascot's colour, bobbing
  (Auren's eyes and antenna light up gold, his thruster cyan); Auren's hand up while listening;
- **error:** red;
- **off:** dim, eyes shut.

Hover shows the status. **Click** opens the panel; **right-click** starts a fresh conversation.

## Panel

Four pages, chosen by the tabs at the top. Built with Omarchy's own `qs.Ui` parts (`KeyboardPanel`,
`Button`, `Toggle`, `PanelSlider`, `PanelSectionHeader`), so it follows the theme and works from the
keyboard: arrows or hjkl move, left/right on the tabs changes page and on a slider changes the value,
Enter acts, Esc closes, Tab goes to the next bar panel. It remembers the last page while the shell runs.

| Page | What | How |
|---|---|---|
| **Home** | the live mascot (**click it to talk**, again to send; right-click to poke; starts Auren when it's off, stops it talking), status, a hint, the last exchange; quick buttons: On, Voice, Mascot, Fresh, Stop | `auren-ctl talk/voice/forget/cancel`, `systemctl --user start/stop auren auren-mascot`, `~/.local/state/auren/history.jsonl` |
| **Mascot** | Auren or Veyra as live previews (click to choose; it says hello in its voice); speech bubble; sleep animation (~6% CPU); voice follows mascot; how faded it gets when idle; size; reset place; poke | writes `mascot`, `bubble`, `sleepAnimation`, `matchVoice`, `idleOpacity` in `~/.config/auren/mascot.json` (the mascot reloads it); size, reset and poke through the mascot's IPC |
| **Voice** | voice replies on/off; reply language (English, Hindi); voice F2 (Auren's), M3, F1, M1 (decision 0001's shortlist; a click plays a sample); speed 0.8–1.4×; quality Fast/Balanced/Best (3, 5, 8 steps); volume; pause between sentences; Test; Defaults | `auren-ctl voice`, `language`, `voice-name`, `tts …`, `say` (the Test sentence is Hindi in Hindi, with the verb gender of the voice) |
| **Brain** | who answers: Auto (Claude, Codex on a limit) / Claude only / Codex only, and which is answering now; Claude's effort (Low, Med, High, XHigh, Max; changing it starts a fresh conversation; greyed out in Codex-only mode); Restart, Log, turn Auren off/on; today's requests, average time to answer, how many Codex answered | `auren-ctl brain`, `auren-ctl effort`, `systemctl --user restart auren`, the log in a terminal, history log |

The daemon keeps voice, brain and the voice tuning in `~/.config/auren/settings.json` and puts them in
`state.json` (`voice`, `voiceName`, `mode`, `tts`), so the panel, the mascot and restarts all agree.
A slider shows its new value as soon as it's released; the daemon's next state confirms it.

**Voice follows mascot** (on by default): picking Auren switches to M3, Veyra to F2, and the
mascot says hello in it. Only when Auren is idle with voice replies on. Choosing a voice by hand still
works; it stays until the next mascot change.

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
- A `JsonAdapter` keeps a property's old value when its key is deleted from the file (only a written value
  changes it), so to switch the mascot by hand, set `"mascot"` rather than removing it.
- Dictation will get its switch here when it's built (probably on Home).
