# Hands

The tools the brain uses to act. Each group is a Python module in `src/auren/hands/`. They run in the daemon's tool runtime, `src/auren/tools.py`, and reach the brain through a thin MCP adapter, `src/auren/server.py`, which forwards each call over a socket. Safety rules: [safety.md](safety.md).

| Group | Module | Status |
|---|---|---|
| System profile (what the brain knows) | `src/auren/profile/` ([system-profile.md](system-profile.md)) | ✅ |
| herdr: coding agents and commands in panes | `hands/herdr.py` | ✅ |
| Desktop and system: apps, windows, screenshots, volume, media, reminders | `hands/desktop.py` | ✅ |
| Screen control: see the screen, mouse and keyboard (fallback) | `hands/screen.py` | ✅ |
| Browser (the user's real Chromium browser via Open Browser Use), routines | `hands/browser.py`, `~/.config/auren/routines/` | ✅ first routine |

## herdr

### Layout

- **Auren has its own herdr workspace, labelled `auren`.** It is created on demand in the background (`--no-focus`), with a home tab. If the user closes it, the next job recreates it. The user's own workspaces (TDS, Obsidian, PROJECTS…) are not relied on and never changed.
- **One tab per job,** named after the job (`lecture-notes`, `review-voisu`). An agent started in it gets the same name, which herdr requires to be `[a-z][a-z0-9_-]{0,31}` and unique; Auren adds `-2`, `-3` when taken.
- **Ownership:** jobs are recorded in `~/.local/state/auren/herdr-jobs.json`, and jobs whose tab was closed are forgotten. `close_job` only closes tabs listed there. Asked to close anything else, it refuses: "Auren only closes tabs it created".
- **The user's agents are found live,** wherever they are, by name, pane id, kind ("codex", "claude code", including misheard "cloud code"), or a hint: project folder, workspace or tab label. Two candidates with no hint means "which one?", listing both.
- **Heavy jobs go to the default coding agent:** `agents.default` in config.toml, else Omarchy's default agent (`~/.config/omarchy/defaults/agent`), else the first installed (Claude Code, Codex, OpenCode, …). The user can name another agent.
- **Without herdr** (not installed, or `integrations.herdr = false`) the agent tools are left out, and `run_command` runs commands in the background instead (`hands/shell.py`, logs in `~/.local/state/auren/jobs/`), with the same results; `job_status` and `close_job` work on both.
- **Approval questions are never answered automatically.** If an agent is `blocked`, the tool returns its screen and the brain tells the user what it's asking. On a timeout or stalled prompt the tool says "may have been delivered; don't resend".

### Tools

| Tool | What it does |
|---|---|
| `list_agents()` | Every running agent: kind, status (idle, working, blocked, done), folder, workspace and tab; Auren's own marked |
| `read_agent(agent, hint)` | The agent pane's recent output |
| `prompt_agent(agent, text, hint, wait)` | Send a prompt, after switching the user's view to the agent (see below). With `wait`, return the agent's reply (the text after the echoed prompt, minus the TUI footer) once it settles |
| `start_agent(kind, cwd, name, task, wait)` | New tab in `auren`, start the agent there, optionally give it a first task. The user's view switches to the new tab once the agent is running |
| `run_command(command, cwd, name, wait)` | New tab in `auren`, run a shell command; the exit code comes from a split marker (`echo "AUREN""_EXIT=$?"`), so the typed command never matches it |
| `command_result(job, timeout_s)` | Wait for a command job to finish; exit code and output |
| `close_job(job)` | Close one of Auren's own tabs |
| `focus(agent, hint)` | Switch herdr's view to that pane and bring herdr's window up |

**Watching agents work.** Giving an agent a task (`start_agent`, `prompt_agent`) brings it in front of the
user: herdr's view switches to the agent's tab (`herdr tab focus` / `herdr agent focus`) and
`desktop.show_herdr()` brings herdr's window up, showing the scratchpad if it's hidden. herdr's window is
found as the window whose process is an ancestor of a `herdr` client (`herdr` with no subcommand). A
terminal that runs every window in one process (foot --server, Ghostty) is ambiguous: then the window
on a special workspace is taken, or none. A scratchpad already showing on another monitor is left
there. A new agent's tab is shown once the agent runs, not before, so keys the user is still typing
don't land in its shell. If herdr isn't open in any window, Auren opens it
the way Omarchy's Super+Ctrl+Return does (`omarchy-launch-terminal herdr`, which attaches to the
running session) on the user's workspace, waits for the window, then switches it to the agent. The
tool result carries `view` ("herdr is showing it", "opened herdr on it" or "not shown …"), so the brain
only says it's showing when it is. It is best effort: if showing fails, the task still goes through. Quick questions with `wait=True`
show the agent too, before Auren waits for the reply.

### Measured (2026-10-10)

| Action | Time |
|---|---|
| Start Codex in a new Auren tab and get the answer to a first question | 8.7 s |
| Follow-up prompt and reply | 2.8 s |
| `run_command` (`uv --version`), finished with exit code | 0.3 s |

### herdr behaviour worth knowing

- **Running outside herdr is fine.** The CLI talks to the user's running session from outside herdr (Auren isn't in a pane), and returns JSON. Errors are JSON on stderr with a `code` (`agent_not_found`, `tab_not_found`, …).
- **`pane run` prints nothing on success.**
- **Wait for the shell first.** Text sent before a new pane's shell is ready is buffered and still runs, but the shell needs a moment before it prints anything. Auren waits for that.
- **`pane wait-output` also searches the echoed command line,** so a marker must not appear verbatim in the typed command.
- **`agent read` returns plain text,** the rendered screen.
- **`agent prompt --wait`** returns at the first settled state (`idle`, `done`, `blocked`). An agent that shows no activity within 5 s returns `agent_prompt_stalled`.
- **Codex's screen shows its usage left** ("5h limit: 36% left · resets at 12:47 PM"). Auren could report this, but Codex's app-server is the cleaner source.

## Desktop and system

The tools use the same commands as the user's Omarchy key bindings, so Auren behaves like the desktop already does.

| Tool | How |
|---|---|
| `launch_app(app, workspace, follow)` | The app is found in the profile by spoken name ("VS Code", "music" → YT Music, "file manager" → Files). Apps run `omarchy-launch-or-focus <class> "uwsm-app -- <id>.desktop"`; web apps run `omarchy-launch-or-focus-webapp <name> <url>`. An app that's already open is focused. With a workspace, the new window is moved there; `follow` decides whether the view goes with it. An app that isn't installed returns "Firefox is not installed" |
| `focus_window`, `move_window`, `switch_workspace`, `close_window` | Window found by class or title words; Hyprland Lua dispatchers: `hl.dsp.focus({ window = "address:…" })`, `hl.dsp.window.move({ workspace = "5", follow = false, window = … })`, `hl.dsp.focus({ workspace = "3" })`, `hl.dsp.window.close({ window = … })` |
| `screenshot(target, save_to)` | `window` (the focused one) and `screen` (the focused monitor) use `grim` with no interaction. `region` uses Omarchy's picker, where the user drags. Default folder `~/Pictures`, like Omarchy |
| `volume(up/down/mute/set, amount)` | `omarchy-audio-output-volume +N/-N/mute-toggle` (shows the OSD); `set` computes the change from the current volume |
| `brightness(up/down/set, amount)` | `omarchy-brightness-display` |
| `media(play_pause/play/pause/next/previous)` | `omarchy-shell media <method>`, Omarchy's MPRIS service (`playerctl` isn't installed). `omarchy-shell media status` gives what's playing; it's also in the live profile |
| `open_url(url, app_mode)`, `open_path(path)` | `omarchy-launch-browser` or `omarchy-launch-webapp`; `xdg-open` |
| `notify`, `reminder(minutes, text)`, `toggle(feature)`, `lock_screen` | `omarchy-notification-send`, `omarchy-reminder`, `omarchy-toggle-*` / `omarchy-bluetooth-power`, `omarchy-system-lock` |
| `system_info(what)` | battery, memory, time, disk, network, CPU, volume, media, from the live profile or `omarchy-network-status` / `omarchy-system-stats` |

Tested live (2026-10-10):
- Omacalc opened straight onto workspace 9 in 0.5 s and was closed again, with the user's view staying on workspace 1.
- A test window was moved between workspaces and closed.
- Window and screen screenshots worked.
- App lookup is right for installed apps, web apps, spoken names and apps that aren't installed.

### Hyprland notes

- **Hyprland 0.56 here is configured in Lua.** `hyprctl dispatch` takes a Lua expression (`'hl.dsp.focus({ workspace = "3" })'`) and prints `ok`. Omarchy falls back to classic dispatchers only for older versions.
- **Launching through `uwsm-app` starts the app via systemd,** so exec rules keyed on the process don't apply reliably. To place a window on a workspace, Auren waits for the new window and moves it.

## Connected to the brain

```bash
uv run auren chat                      # type requests; see tool calls and the spoken reply
uv run auren ask "open whatsapp on workspace 2"
```

- **The brain** (`src/auren/brain/`) keeps one Claude Code process running: Haiku 5.5, thinking on, effort low. It uses Auren's instructions (`brain/prompt.py`) plus the static system profile (~900 tokens). Every message carries the live snapshot (~220 tokens).
- **Tools:** only Auren's tools (`--tools ""`, `--mcp-config` → `python -m auren.server`, `--allowedTools mcp__auren`). The adapter forwards to the tool runtime: the daemon's, or for `auren chat` one of its own (its own session).
- **Fresh conversation** after 40k tokens of context.
- **Replies are cleaned for speech:** backticks, bold and bullets are removed, and the brain is told to speak plainly without paths or commands.
- **Isolation from the user's own Claude settings:** the process runs from `$XDG_RUNTIME_DIR/auren/brain` (outside `$HOME`) with `--setting-sources local`. Claude Code loads `.claude/CLAUDE.md` from **every parent folder**, so a working folder under `$HOME` picks up the user's personal `~/.claude/CLAUDE.md` even with `--setting-sources project`. That happened once: the brain started Codex with the user's personal `codex exec` flags. Checked afterwards: the brain reports no CLAUDE.md content.

Tried live (2026-10-10, 2.3–4 s per request; delegating to Codex ~10 s):
- which agents are running;
- open Omacalc on workspace 9 while staying put;
- close it (asked, then closed after "yes");
- the Omarchy region-screenshot command;
- `uv sync` with its exit code;
- "delete all recordings" (listed, asked, kept after "no");
- "ask Codex to write a poem into a file": start_agent in an Auren tab, then checked, read and closed;
- "open whats app on work space nine but stay here" (misheard words).


## Browser

**What it runs on.** The tools drive the user's real, logged-in browser through **Open Browser Use**. Which browser (`hands/browsers.py`):
- **The browser:** `browser.name` in config.toml, or with `auto` the system default (`xdg-settings get default-web-browser`) if it's a Chromium browser Auren knows (Chrome, Chromium, Brave, Helium, Edge, Vivaldi), else the first one installed. The brain can name another for one request.
- **Its profile** (bookmarks, download folder): `browser.profile`, or the last one used, from the browser's `Local State`.
- **Its Open Browser Use host:** the default socket folder `/tmp/open-browser-use`, unless `browser.sockets` names another for that browser. People who run one host per browser set it, for example `sockets = { chrome = "/tmp/open-browser-use-chrome" }`; a bare `obu` command goes to the default one, which is why it once "got confused about which browser".

Open Browser Use can't pick a browser by name (its `--browser` knows only Chrome, Chrome Beta and BitBrowser), so with two browsers connected to the same socket folder Auren can't tell which one answers. Give each its own folder with `browser.sockets`.

Without Open Browser Use (or with `integrations.browser = false`) the browser tools are left out and the brain is told it can only show pages.

| Tool | What it does |
|---|---|
| `browser_open(url, browser)` | New tab in Auren's own session (tab group "auren"), then read. `url` may be `bookmark:<folder/name>`; the URL comes from the browser's `Bookmarks` file |
| `browser_read()` | Page text (first 5,000 characters) plus numbered clickable items: links, buttons, fields. Image-only links are labelled `image: <file>` with their target. Identical labels get their row's name: "More actions (Alt+A) (Week 3)" |
| `browser_click(target, expect_download)` | By number or by words of the label. `target=_blank` links open in the same tab. `expect_download` watches the profile's download folder (from its `Preferences`, else `~/Downloads`) for a new finished file and returns its path |
| `browser_type(target, text, submit)`, `browser_scroll` | Type into a field; scroll |
| `browser_bookmarks`, `recent_downloads`, `browser_close` | Bookmark names (URLs with long tokens are shortened in output); recent files; close Auren's tabs |

**Safety.** Clicks on buy, pay, send, post, submit, delete and similar labels need confirmation (at the default approval level, see [safety.md](safety.md#approval-levels)). Page text is wrapped as untrusted.

**Lessons from the first real site (IITM Seek, Google Drive):**
- **Chrome doesn't lay out background tabs,** so their visible text is empty (Drive rows read as nothing). Auren brings its tab to the front before reading or clicking (CDP `Page.bringToFront`).
- **Links can be zero-sized** while the image inside them shows, and a link's "text" can be a single space. Both need care when listing clickable items.
- **Drive downloads a folder as a zip,** after a few seconds of preparing.

## Routines

User workflows as Markdown files in `~/.config/auren/routines/*.md`, loaded into the brain's instructions.

First routine, `mlp-slides.md` (verified): Chrome bookmark Courses/MLP → Supplementary Contents → Lecture PPTs/Slides → "Download Now!" (an image link to the Google Drive "Slides" folder) → More actions (Week N) → Download. Through the brain, "download the week 3 MLP slides" took 6 tool calls and 29 s, and gave `~/Downloads/Week 3-….zip`.

## Scratchpad

herdr lives in a foot terminal on Omarchy's scratchpad (`special:scratchpad`). Hyprland opens new windows on whatever is showing, so an app launched while the scratchpad was open landed inside it. `launch_app`, `open_url` and `open_path` now hide a showing special workspace first, so new windows land on the user's normal workspace. `launch_app` only moves the user's view when asked (`follow=False` by default).

## Backup brain

`Brain` (used by `auren chat`) runs Claude first. On a usage-limit error it switches to `CodexBrain`, which runs GPT-6 Luna at low effort with the same tool server through `codex exec`, and it retries Claude after 30 minutes.
- Codex can't replace its own system prompt, so Auren's instructions go in the message with the last 4 exchanges.
- Its MCP adapter only needs the runtime's socket and the session (`AUREN_TOOLS`, `AUREN_SESSION` via `mcp_servers.auren.env`): the tools themselves run in the daemon, with the desktop session's environment.
- Pending confirmations live in the runtime, and each message lists them in `<awaiting_confirmation>`, so a "yes" works even though every Codex run is fresh.
- It only takes over before anything has been done in that request (see [safety.md](safety.md#stopping)).
- Speed: ~13–17 s per request against ~3–5 s for Claude. `auren chat --backup` forces it.

## Lessons from the user's first voice test (2026-10-10)

- **"Open WhatsApp on workspace 3" means go there.** `launch_app` follows the window by default again; it stays put only when the user says so ("keep me here").
- **Browser popups (Chrome's "Download multiple files: Allow / Block") are browser UI, not page content.** Page tools can't see or click them. A download that doesn't start within 60 s is now reported as "the browser is probably showing a popup; ask the user". The user clicked Allow, so Chrome now remembers it for drive.google.com.
- **Never report an action without checking it.** Brave answered "close" with its own "Close all tabs?" dialog, and Auren said "closed". `close_window` now checks the window is gone, and otherwise reports that the app is showing its own confirmation.
- The brain can now see the screen and use the mouse (below), so it can answer such popups when the user asks.

## Screen control (mouse and keyboard)

The fallback for what no other tool reaches: browser permission popups, apps' own dialogs, apps with no command line.

| Tool | How |
|---|---|
| `look_at_screen()` | `grim` captures the focused monitor at 1024 px wide, with a light labelled grid every 50 px (numbers along the top and left). Returned as an image the brain sees |
| `click_at(x, y, target, button, double)` | Image pixels → layout coordinates (monitor position + scale). Moves with Hyprland (`hl.dsp.cursor.move`), clicks with a virtual mouse made through `/dev/uinput` (python-evdev; the user can write to `/dev/uinput`), then puts the cursor back. `target` says what is clicked. Labels like buy, pay, send, post, submit, delete need confirmation |
| `scroll_at`, `type_text`, `press_keys` | Wheel via the virtual mouse; text and shortcuts via `wtype`. Alt+F4, Ctrl+Q, Ctrl+W and similar need confirmation |

**Brain rules.** Use these only as a fallback, or when the user asks; look first; click by the grid numbers; look again to verify; say what was clicked.

**Tested 2026-10-10:**
- A local test page in Chrome: "click the big green button with the mouse" → look, `click_at(512, 345)`, look → "It turned red and now says CLICKED!" (8.6 s).
- The virtual mouse shows up as a Hyprland input device (`auren-virtual-mouse`), and a terminal with mouse reporting received a real press and release.

**Gotcha.** `grim -s` scales from the monitor's **layout** size (1536×864 on this 1920×1080 panel at 125 %), not from physical pixels. Computing the factor from physical pixels made every click land at 80 % of the target. The grid makes coordinates readable regardless, and the real image size is now taken from the image itself.

## Web apps and screen typing (user test, 2026-10-10)

The user opened Discord (an Omarchy web app) and asked Auren to message someone. Auren opened
discord.com in a Brave tab instead and typed the message there (it asked before sending). Two causes:

- **Typing didn't land in the web app.** Omarchy has focus-follows-mouse (`input:follow_mouse = 1`).
  `click()` put the cursor back afterwards, so focus followed it to another window and `wtype` typed
  there. Now the cursor stays where Auren clicked. `type_text`/`press_keys` also re-focus the
  last-clicked window if focus has moved. Tested live: click a window, move the cursor to another one,
  type: the text lands in the clicked window.
- **The browser tools can't reach web app windows.** Web apps are Brave `--app` windows. Open Browser
  Use lists their tabs (`user-tabs`), but `claim-tab` fails with "Grouping is not supported by tabs in
  this window" because it always puts a claimed tab into a tab group. The extension comes from the
  Chrome Web Store, and Chrome disables store extensions whose files are edited, so fixing this means
  running our own unpacked copy. Not done. For now the brain's rules say to work in the web app's
  window with screen control and not to open the same site in a tab.
- If a claimed tab is ever used: `finalize-tabs` closes every session tab not listed in `--keep`, so a
  claimed user tab must always be kept with status `handoff`.

## Browser not open yet (user test, 2026-10-10)

"Download the MLP week 1 PDF" with Chrome closed: the brain opened Chrome and retried right away, but
the Open Browser Use extension needs a few seconds after a browser starts before it connects, so it
gave up. Now `browser.obu()` handles it: on "not connected" it starts the browser if it isn't running
(`uwsm-app -- google-chrome-stable`, scratchpad hidden first), pings the extension every 0.5 s for up
to 25 s, then reruns the command once. If the browser is running but the extension never connects, it
says so (extension off, or a profile without it). Tests: `tests/test_browser.py`. Not yet tried with
Chrome actually closed (that would close the user's browser).

## Dragging (user test, 2026-10-10)

"Create a yellow circle" in Excalidraw: the brain picked the ellipse tool and yellow, then got stuck,
because screen control could only click, and it offered to install a mouse tool. Added `drag_at` (press,
move, release, optionally holding shift/ctrl/alt; Shift = perfect circle). Two findings:
- Cursor warps (`hl.dsp.cursor.move`) while a button is held give apps no motion events, so the drag
  uses relative motion from the virtual mouse. It steers by reading the cursor back after each step and
  learning the gain, because pointer acceleration applies to it.
- `wtype` releases its keys when it exits, so held modifiers come from a second small uinput device
  (now `auren-virtual-keys`, which also has the digit keys for `press_code`).

Tested on a full-screen test surface: drags land within 1 px (400,300→701,500; 900,600→649,250;
200,700→1300,150), apps get 40–60 motion events, and Shift is held from press to release. The brain's
rules now say it has full mouse control and must never offer to install mouse tools.

## Excalidraw (2026-10-20)

Drawing with `drag_at` makes one rough shape per call, so "draw a cat" or "draw my app's architecture"
never came out right. Excalidraw has a better way in. If you paste JSON of type `excalidraw-api/clipboard`,
it runs the elements through `convertToExcalidrawElements`, its own skeleton API: it measures and wraps
labels, binds arrows to shapes and fills in every default. If you paste Mermaid text, it lays the diagram
out with mermaid-to-excalidraw. `hands/excalidraw.py` uses both, so a whole drawing goes in with one tool call.

| Tool | How |
|---|---|
| `excalidraw_draw(elements \| mermaid, place, clear)` | Builds the skeletons, puts them on the clipboard (`wl-copy --sensitive`), and presses Ctrl+V over the canvas. Then Ctrl+C copies the selection back, which proves the paste landed. Shift+2 scrolls it into view. The user's clipboard is put back afterwards |
| `excalidraw_read()` | Ctrl+A, Ctrl+C: the canvas as Excalidraw's own clipboard JSON, cut down to ids, boxes, labels, arrow ends and every style that isn't the default (colours, fonts, rotation, opacity, fill style, arrowheads, frames), in the form `excalidraw_draw` takes, so a drawing read back redraws as it was |

What the builder adds on top of Excalidraw's format:
- Colour names from Excalidraw's palette (`light-blue` fill, `dark-blue` stroke, `blue` in between), and
  `font` hand / normal / code.
- Points in canvas coordinates, made relative for Excalidraw. A line that ends where it started is a
  polygon and can be filled.
- Arrows that only name their shapes. Excalidraw binds an arrow but keeps the coordinates it gets, so the
  builder aims it, edge to edge, on the rectangle, ellipse or diamond outline. `elbow: true` tries four
  right-angled routes and takes the one crossing the fewest other shapes.
- Shapes with a label but no size get one that fits the label. Boxes with labels are rounded, unlabelled
  parts (a house wall, a door) are sharp.

**Placement.** Excalidraw always pastes at the pointer and ignores the elements' own coordinates. Before
pasting, the tool copies the canvas. If there's something there, it resets the zoom to 100 % (Shift+0),
pastes, reads back where the paste landed, and if that's wrong, deletes it, pans the canvas by the
difference with middle-button drags, and pastes again. Before deleting, it copies the selection once more
and stops if it isn't exactly the paste (the user may have clicked something meanwhile). A miss also measures the real zoom (a browser
zoom level breaks the 1:1 guess), and the second try corrects for it. `place="beside"` puts a new drawing
100 px right of what's there. `place="exact"` keeps the brain's coordinates, for additions to a drawing it
read. Arrows may start or end at shapes already on the canvas. They are aimed from the canvas geometry but
can't be attached, because Excalidraw only binds within one paste, and the result says so. If three moves
don't get it within 2 px, the tool says so instead of reporting success. "Beside" measures rotated shapes as
they really reach. Locked elements aren't selected by Ctrl+A, so they're invisible to placement.

**Clear.** `clear` empties the canvas first. If everything on it is something Auren pasted in this run
and nobody changed since (id and version remembered from the copy-back; Excalidraw raises an element's
version on every edit), it just does it. Otherwise it asks (kind `files`), bound to the ids and versions
it saw. If the canvas can't be read (Excalidraw closed, which reopens the user's saved drawing, or the
copy failed), it clears nothing and draws beside. The new drawing is pasted and checked before anything is
deleted: then Ctrl+A, Delete, and the same paste again, so a drawing that won't paste leaves the old one.

**Gotchas found live:**
- Paste only works with the pointer over the canvas and no text box focused, so the tool points at an
  empty spot (left of the welcome screen's menu, below the middle), presses Escape, Escape, V and clicks.
  The menu is about 300 px wide in the centre of an empty canvas and a pointer over it doesn't count as
  on the canvas: in a half-screen window the old spot (30 % across) landed on its Help button and every
  paste was dropped. The spot is now at most 200 px left of the middle.
- The window is the one whose class says Excalidraw (the web app), else a browser window whose title
  starts with "Excalidraw Whiteboard" (the page is the visible tab). Matching the word anywhere in a title
  would pick a terminal on the `excalidraw-drawing` branch. A background tab can't be seen, so the web app
  opens instead (same site storage, same drawing). Tested in a normal Brave tab too.
- The clipboard is saved in one format (text, else PNG, else whatever was offered, such as copied files)
  because `wl-copy` serves one. Rich text comes back as plain text.
- `wtype` uploads its own keymap, so apps that read the physical key (`event.code`) see the wrong one.
  Excalidraw's Shift+2 and Shift+0 are such shortcuts, so they come from the uinput keyboard
  (`screen.press_code`, digits only, which sit on the same keys in every layout). Letters stay on `wtype`.
- Mermaid converts in the background. A Ctrl+C before it lands copied the previous selection, which then
  got deleted as "the paste" when moving it. Now a copy only counts if every id in it is new.
- Excalidraw puts selection changes in its undo history, so one Ctrl+Z doesn't undo a paste.
- `wl-copy` forks to serve the clipboard. It must not inherit our pipes, or `subprocess.run` waits forever.

**Tested 2026-10-20** (in a throwaway Brave profile, `uv run auren ask`):
- "Draw a cute cat sitting next to a house": one `excalidraw_draw` with 23 elements, then one look, 30 s.
  A grey cat with ears, whiskers and tail beside a yellow house with a red roof, placed beside the
  drawings already there.
- "Make a diagram of how a voice assistant works": Mermaid flowchart, 20 s, laid out correctly. It was
  black and white until the rules told the brain to colour nodes with `classDef`.
- "Add a box called Mascot below the brain": `excalidraw_read`, then `place="exact"`. The box landed
  centred under Brain. Exact placement lands within 2 px (wanted 764,560, got 764,558). Placing a paste
  1959 px away took two pans.
