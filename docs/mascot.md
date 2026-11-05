# Mascot

A small character floats over the desktop and shows what Auren is doing: **Auren** (a hover-bot, the
default) or **Veyra** (a fox spirit). Pick one on the bar panel's
Mascot page, or set `mascot` in `~/.config/auren/mascot.json`; it switches live.

| | |
|---|---|
| Code | `mascot/shell.qml` (windows, input, bubble), `mascot/mascots/<name>/` (the characters), `mascot/mascots/common/` (what they share) |
| Runs as | `auren-mascot.service`, a systemd user service: `qs -n -p ~/.local/share/auren/mascot` |
| Reads | `$XDG_RUNTIME_DIR/auren/state.json` (state, live transcript, activity, caption, yes/no question), `$XDG_RUNTIME_DIR/auren/level` (mic or voice loudness, ~20/s) |
| Remembers | `~/.local/state/auren/mascot.json` (place, size, hidden) |
| Preferences | `~/.config/auren/mascot.json` (optional): `mascot` auren, `idleOpacity` 0.45, `fadeAfter` 12 s, `sleepAfter` 240 s, `bubble` true, `sleepAnimation` true, `matchVoice` true (read by the bar panel) |

## The characters

![Auren and Veyra in each mood](images/mascots.png)

| | Auren | Veyra |
|---|---|---|
| Is | a cobalt hover-bot: dark glass visor with gold LED eyes, antenna, floating hands, thruster glow | a lavender fox spirit: big ears, glossy eyes with lashes, a gold star on her forehead, a fluffy tail |
| Listening | radio rings from the antenna, a hand cupped at his ear | ears perk and twitch with your voice, the star glows, rings |
| Thinking | hand on his chin, two sparks orbit the antenna | looks up, tail curls, sparkles twinkle |
| Working | types on a hologram keyboard; eyes narrow to a scan | paws tapping, tail wagging fast |
| Using the mouse | holds the pointer in his right hand | a clicking cursor by her side |
| Speaking | the mouth is a little equalizer | mouth opens with the voice, little hops |
| Yes/no | head tilt, scratching his head, "?" | head tilt, one ear folded, "?" |
| Error | red visor, X eyes, sparks | ears flat, X eyes |
| Asleep | sinks to the ground, thruster off, dim eyes | ears droop, eyes ‿ ‿, tail wrapped down |
| Voice (if "voice follows mascot") | M3 | F2 |
| Colour (bar icon, panel accents) | gold `#F5B83D` (body cobalt) | lavender `#A08BF7` |

## What it shows

Both characters show every state (their poses are in the table above); the bubble is the same.

| Auren is | Mascot | Bubble |
|---|---|---|
| starting | wakes up | "Waking up…" |
| idle | hovers, blinks, glances around; fades after 12 s; falls asleep (z's) after 4 min | — |
| listening | rings that grow with your voice | the live transcript |
| thinking | thinking pose | what you said |
| working | typing; holds a clicking pointer when it uses the mouse | what it's doing ("Opening WhatsApp", "Clicking Allow button") |
| speaking | moves with the voice's loudness | the sentence being spoken (captions) |
| waiting for a yes/no | head tilt, "?" | the question(s), a **Yes** and a **No** button (the only part of the bubble that takes clicks), and "Or Super+Alt+A, then say yes or no" |
| error | X eyes | the error |
| off (daemon not running) | grey, asleep | on hover: "click to start" |

After a reply it smiles (^ ^).

## Using it

- **Drag** to move it anywhere; the place is remembered.
- **Click** it to talk (same as Super+Alt+A); click again to send. While Auren thinks, works or speaks, a click stops it (the bubble says "Press Super+Alt+A to stop"). When Auren is off, a click starts it.
- **Right-click** for a poke (it hops).
- **Scroll** over it to resize.
- **Hover**: it wakes up, looks at the pointer, and shows the hint.

Remote control (for the bar plugin):

```bash
qs -p ~/.local/share/auren/mascot ipc call -- mascot toggle   # show, hide, poke, reset, state, size 1.2
systemctl --user restart auren-mascot
```

## How it's built

- **Quickshell** (QML), the same toolkit as the Omarchy shell, run as its own small instance so a
  mascot bug can't affect the bar. It's a layer-shell surface on the overlay layer: on every
  workspace, never takes keyboard focus, not in the window list.
- **Input mask** = the body's mouse area outside a held gesture, so clicks around it reach the windows below. The bubble is a
  second layer surface that normally passes clicks through, with an input region for Yes/No buttons while a confirmation is open.
- **Dragging:** the transparent layer surface stays screen-sized at a fixed origin. Only the mascot
  moves, so pressing, dragging and releasing never resize or move the Wayland surface. The input mask
  follows the same body rectangle as the mouse area; it covers the screen during a held gesture so a
  fast pointer can't outrun it. Outside a gesture, the rest of the desktop remains click-through.
  `MascotDragArea.qml` measures each event in the fixed surface coordinate space, applies Qt's drag
  threshold, and keeps the original grab offset through edge clamping and direction changes. Only
  the left button drags. Cancelled or interrupted drags save their last position and never trigger talk.
- The mascot waits for its saved position, character and screen geometry before appearing. Resizing,
  switching characters or changing screen geometry cancels any active drag and clamps the position
  to the available screen. Saved coordinates are written together after the gesture, not per motion.
- **Animation:** every pose is oscillators driven by one clock × mood weights that fade in and out,
  so moods blend instead of jumping. The clock runs every frame only while Auren is doing something.
  Idle breathing is stepped at 10 fps, and the clock stops once the mascot has faded. All of this is in
  `mascots/common/MascotBase.qml`; each character only adds its pose and drawing.
- **Fading** draws the mascot as one picture (`layer.enabled` while its opacity is below 1). Without it,
  Qt fades each part on its own and overlapping parts (Veyra's ears and tail, Auren's hands) show
  through the body.
- **Captions and bounce:** the voice keeps a timeline of when each sentence's audio actually plays and
  its loudness envelope; a small thread reports the current sentence and level (`src/auren/voice.py`).
  The mic level comes from the audio chunks the ears already read (`src/auren/ears.py`).
- **Activity phrases:** `src/auren/live.py` turns tool calls into short phrases.
- While the mascot is on screen with its bubble on, its bubble is the only place for listening, thinking, stopped, replies and errors. Otherwise the daemon shows the same news itself (`src/auren/notice.py`): short statuses on Omarchy's OSD, each with its own icon, and replies and errors as notifications. What Auren does (clicks, drags, typing, opening an app...) also shows on the OSD while the bubble is up.
- Bubble colours follow the current Omarchy theme (`~/.local/state/omarchy/current/theme/colors.toml`).
- Long bubble text keeps the newest lines visible within a five-line limit. Control hints stay pinned
  below the text. Appended lines slide up; replacement messages appear immediately.

## Measured (2026-10-10)

- **CPU:** ~6% of a core with 60 fps idle breathing in the first version, so the clock was changed:
  ~1% while awake and idle, ~0.6% once faded. The user wanted the smooth sleeping animation back, so
  `sleepAnimation` (default on) keeps the smooth clock and drifting z's while faded or asleep: ~6% of a
  core. Set it to false in `~/.config/auren/mascot.json` for the cheap mode; the bar plugin will toggle it.
- **Memory:** ~104 MB private. A bare Quickshell window is already ~78 MB, so the mascot itself is
  ~25 MB. Moving it into the Omarchy shell process as a plugin would save the ~78 MB; that's
  worth doing with the bar plugin if memory gets tight.

## Gotchas

- The former drag implementation changed the layer surface between mascot-sized and screen-sized
  and guessed each event's origin. On empty workspaces it could lose the grab; during reconfiguration
  the character could briefly appear at the screen origin. The fixed surface removes both transitions.
  Dragging no longer depends on a layer-animation rule. The existing optional rule can stay if you
  prefer instant mascot appearance:
  `hl.layer_rule({ match = { namespace = "^auren-mascot" }, no_anim = true, animation = "none" })`.

- Cursor warps (`hyprctl dispatch hl.dsp.cursor.move`) during a button press don't produce motion
  events for clients. To test dragging, use relative motion from a virtual mouse (`REL_X`/`REL_Y`).
- Quickshell hot-reloads on save. Restart the mascot service when switching to this fixed-surface
  implementation, so the previous surface is recreated.

## Testing dragging

The Qt interaction tests exercise the production `MascotDragArea` without a running desktop or
daemon. They cover click jitter, fast movement, reversing at edges, fractional coordinates,
cancellation, hiding/disabling, right-clicks, mixed buttons and repeated drags. CI runs them separately
from the Python suite:

```bash
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software /usr/lib/qt6/bin/qmltestrunner -input tests/mascot
```

For compositor checks, use an isolated copy of `mascot/` with temporary XDG config/state folders,
stub the talk action, and send real relative motion from a virtual mouse. Wait until Qt has received
the button press before sending motion; the input stack can delay a synthetic press. Check dragging
on an empty workspace, over a test app and outside its bounds, with both mascots and sizes 0.6, 1 and
2.2. Check that background clicks pass through before and after dragging, hiding/resize cancels a grab,
and a restart restores the dropped position. Restore the workspace and cursor when finished.

## Making a mascot

A mascot is a folder under `mascot/mascots/<name>/` with `<Name>.qml`, whose root is a `MascotBase`
(`mascots/common/`). The base holds the contract with `shell.qml` and the bar panel:
- **in:** u, mood, level, drowsy, asleep, sleepAnimation, mouse, awaiting, hovered, pointerX/Y, off;
- **out:** implicitWidth/Height, padX/padTop (where its stage starts), hitbox (an Item on the stage, the
  input mask), bubbleAnchorY, accent;
- **functions:** `poke()`, `cheer()`;
- **for drawing:** the clock `t`, `osc(hz, phase)`, `pulse()`, `mix()`, mood weights (`wListen`, `wSway`,
  `wWork`, `wSpeak`, `wAsk`, `wSleep`, `wError`…), `lv` (smoothed loudness), `hopA`/`squashA`/`shakeA`,
  `happy`, `blink`, `lookX/Y`, `eyeShape`, `breath`. Shared pieces: `Zzz`, `Pointer`, `Sparkle`.

Then add it to `kind` and the components in `shell.qml`, `mascots` in the bar panel, and pixels in
`omarchy-plugin/MiniMascot.qml`.

To see every mascot in every mood (it's how Veyra and Auren were drawn and checked):

```bash
qs -p mascot/gallery.qml                                    # on screen; right-click one to poke it
GALLERY_OUT=/tmp/g.png qs -p mascot/gallery.qml             # save a PNG and quit
GALLERY_U=10 GALLERY_ONLY=idle,speaking GALLERY_BG=#efece4 GALLERY_OUT=/tmp/g.png qs -p mascot/gallery.qml
```

Quickshell only knows a folder's QML types when that folder is imported somewhere, so `shell.qml` and
`gallery.qml` import every mascot folder even though they load them by name.
