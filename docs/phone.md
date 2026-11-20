# Phone remote

The Android app (`android/`) is a remote for the Auren on your PC: talk or type to it from your phone,
see what it's doing, answer its questions, stop it, and get what it sends you. The PC does all the work;
the phone is a microphone, a screen and a few buttons. The plan behind it is [phone-plan.md](phone-plan.md).

It works with Auren on Omarchy and on macOS. On a Mac the pairing is in the menu bar icon's **Phone…**
window instead of the bar panel, and the firewall is macOS's own: see [On macOS](#on-macos).

## Setting it up

1. Put the phone on the PC's Wi-Fi, or install Tailscale on both (signed in to the same tailnet) to reach
   the PC from anywhere. The PC's server listens on its Tailscale address (`tailscale ip -4`) and on its
   private LAN addresses (10/8, 172.16/12, 192.168/16, on interfaces with a network card behind them, or a
   bridge over one), never on `0.0.0.0`, a public address, or Docker/VM/VPN interfaces. Either path is enough; with both, the app uses Wi-Fi at
   home and Tailscale away. It listens only while a phone is paired or a pairing is open: until you run
   `auren phone pair`, nothing on the network can reach it.
2. Check the firewall: run `auren doctor` and look at its **Phone** section. Omarchy turns on `ufw`,
   which drops incoming connections, so if nothing lets port 8735 in, the app can't reach the PC. Often
   something already does: Tailscale lets its own traffic past ufw, and a rule that allows everything from
   your LAN covers the port too. Doctor knows both. If it warns, it prints the commands for your own
   addresses, which look like this (LAN and Tailscale only):

   ```sh
   sudo ufw allow from 192.168.0.0/16 to any port 8735 proto tcp comment 'auren phone'
   sudo ufw allow in on tailscale0 to any port 8735 proto tcp comment 'auren phone'
   ```

   On a Mac, see [On macOS](#on-macos) for its firewall.
3. Install the app: on the bar panel's **Phone** page, click **Get the app** and scan the QR code with the
   phone's camera (or run `auren phone app` in a terminal). It downloads `auren-phone-X.Y.Z.apk` for the
   Auren you have, from its GitHub release; a build that isn't a release links to the
   [latest release](https://github.com/kvm404/Auren/releases/latest) instead. Open the download (Android asks
   you to allow installing from your browser once). A newer release's APK installs over it, keeping the
   pairing. To build it yourself: `cd android && ./gradlew installDebug` (a debug build can't update a
   release one: uninstall that first), with `android/native/build-phonon.sh` first for the phone's own voice.
4. On the PC, open the bar panel's **Phone** page and click **Pair a phone** (or run `auren phone pair` in a
   terminal). It shows a QR code; the terminal also prints the same thing as text (`auren://pair?...`), and
   waits. Scan the QR with the app, or paste the text. You don't need to pair again when Tailscale comes or
   goes: the PC remembers its Tailscale address from whenever Tailscale was last up, and the code (and each
   hello) carries it even while Tailscale is off. On a PC whose Tailscale has never been up, the phone picks
   the address up the first time it connects after Tailscale is on. The code works once, for 5 minutes, and 5 wrong
   codes cancel it. The QR also carries every address the server listens on and the fingerprint of the
   PC's certificate, which is what the app trusts from then on.
5. The panel's Phone page also shows where the server listens, which phone is connected and over which
   path, and unpairs a phone. `auren doctor` has a **Phone** section: Tailscale and Wi-Fi/LAN (each a warning when missing, never a
   failure while the other works), every address the server listens on or why it can't, the firewall, and
   the certificate.

| Command | Does |
|---|---|
| `auren phone pair [--no-wait] [--no-qr] [--json]` | A new one-time code (any earlier one stops working). It opens the pairing, which starts the daemon's server, and waits for it to listen. `--json` (the bar panel): a line `{uri, qr, expires, where, warn}` (`qr` a PNG, deleted after), then `{paired}`, `{expired}`, `{error}` or, on SIGTERM, `{cancelled}` |
| `auren phone app [--json]` | A QR code to download the app for this version (`--json`, the bar panel: `{url, qr, version}`, `qr` a PNG) |
| `auren phone devices [--json]` | The paired phones: id, name, when paired, last seen |
| `auren phone revoke <id>` | Unpair one. Its token stops working at once and its open connection is closed |

`config.toml`:

```toml
[phone]
enabled = "auto"   # auto or true: serve on Tailscale and the LAN, whichever is there; false: never
port = 8735
lan = true         # false: Tailscale only, nothing on the Wi-Fi
```

The server checks its addresses every 5 s and moves with them (a new Wi-Fi, Tailscale coming up); with
none it stays off. `auren config set` for any of these takes effect on the next reload: the server
starts, stops, or moves to the new port, and phones connected to the old one are dropped. `send_to_phone` and its prompt rule are offered to the brain only while at least one phone is
paired (pairing or revoking the last phone starts a fresh conversation so the brain's tools match).

## On macOS

- **Pairing**: the menu bar icon → **Phone…** opens a window with what the bar panel's Phone page has:
  where Auren listens (and a note when Tailscale is off), **Get the app**, **Pair a phone** with its QR
  code and countdown (**Cancel**, or closing the window, ends the code at once), and the paired phones
  with **Unpair**. It runs `auren phone pair --json` with Auren's own Python and reads the same files.
  The `auren phone` commands work in Terminal too.
- **Addresses**: Wi-Fi and Ethernet (a USB or Thunderbolt adapter, an iPhone over USB) come from
  `ifconfig`, kept only on the interfaces `networksetup -listallhardwareports` lists (if it doesn't
  answer, only `en0`, `en1`...). Tunnels (`utun`,
  which is also Tailscale's), bridges (the Thunderbolt Bridge, Internet Sharing, VMs), AirDrop
  (`awdl`, `llw`) and link-local `169.254` addresses are left out, as on Omarchy.
- **Tailscale**: the Mac app (from the App Store or tailscale.com) or Homebrew's `tailscale`. Auren
  finds its command in your PATH, in Homebrew's folders, or inside Tailscale.app, so the standalone app
  needs no CLI installed (it's run with `TAILSCALE_BE_CLI=1`, which that binary needs to act as the
  CLI). Turn Tailscale on from its menu bar icon.
- **The firewall**: macOS's Application Firewall (System Settings → Network → Firewall) is off unless
  you turned it on. When it's on, the first time Auren listens for a phone (when you pair one) macOS
  asks whether **python3.x** (or **Auren**) may "accept incoming network connections". Click **Allow**.
  If you clicked Deny, or the phone can't connect: System Settings → Network → Firewall → **Options**,
  find python3.x or Auren in the list (add it with **+** if it isn't there) and set it to **Allow
  incoming connections**. **Block all incoming connections** stops every phone, whatever the list says.
  `auren doctor`'s Phone section asks the firewall (`socketfilterfw`) and says which of these it is: it
  says allowed only when the firewall is off or its list of apps allows Auren's Python or Auren.app. A
  reinstall that brings a new Python can make macOS ask again.
- **Downloads and the clipboard**: an image shared to Auren is saved in `~/Downloads`; the PC clipboard
  action uses the Mac's pasteboard (an image goes on as PNG), and Open on PC uses `open`.

## How a phone turn works

- **One turn at a time, across the desk and the phone.** A turn is owned from the moment listening starts
  (or a typed request comes in) until the answer ends. While the desk owns it, the phone's requests get
  `busy`, and so does a second ask or talk from the phone that owns it; while the phone owns it, Super+A, `auren-ctl talk/start/ask` say "Auren is busy with your
  phone". **Stop works from either side**: the phone's Stop, or at the desk Super+A while it thinks or works, the
  panel's Stop or `auren-ctl cancel`.
- **The desk's safety level applies.** Approvals show on the phone (Yes / No) and on the desk (bubble,
  panel, `auren-ctl yes|no`); either answer counts, the same way as [the buttons](safety.md#who-reads-the-answer),
  and what follows (the action, the brain carrying on) is a turn of the side that answered.
- **Replies go to the phone that asked, as text**, and the phone may read them aloud in Auren's voice
  ([below](#the-phones-voice), a setting). The PC
  doesn't speak a phone turn's reply and doesn't pop up notices for it; the mascot still shows the state.
- **Voice:** the phone records 16 kHz mono 16-bit PCM and streams it over the WebSocket into the same
  speech pipeline as the PC's microphone (AssemblyAI). Hold to talk, or hands-free: it sends when you stop
  talking, like auto-send.
- **Hands-free is a conversation.** After Auren's reply (once the phone has finished reading it aloud, and
  the PC is idle), the app opens the microphone again by itself, so you can keep talking back and forth. A
  yes/no question doesn't stop it: say "yes" or "no". It turns off when you tap Hands-free again or Stop,
  when a listen hears nothing for 12 s, when the PC is busy at the desk, on an error, or when the link
  drops. The app says why ("Hands-free off: you went quiet"). The decision is `listenNext` in
  `android/.../Conversation.kt`.
- **It keeps going with the screen off**, or with another app in front, like a call. While it's on, a
  foreground service (`ConversationService`, type microphone) keeps the link and the microphone, and a
  partial wake lock keeps the CPU awake between turns. A "Hands-free on" notification shows it, with an
  **End** button (Android 13 and newer ask once to allow notifications). The service stops the moment the
  mode goes off. Hold-to-talk still stops when the app leaves the screen. Some phones (Vivo, Xiaomi and
  others) close background apps hard: if hands-free stops with the screen off, allow Auren to run in the
  background in the phone's battery settings.
- **Fresh** (Home's top bar, or Settings) starts a fresh conversation on the PC and clears the phone's
  chat. A request still waiting is dropped: its reply isn't shown or read aloud. The inbox stays.
- The brain is told the request came from the phone, so it keeps replies short and uses `send_to_phone`
  for links, files and images.

## The phone's voice

With "Read replies aloud" on, the phone reads replies itself with Gradium's Phonon 31m (CPU, through
[xn-ptts](https://github.com/gradium-ai/xn-ptts) v0.7.1's C library), in the same voice as the PC: one of
Everly (the default), Juniper, Hugh and Trevor. The app's Settings has the picker; it sends `voice.set`, and
the phone reads in the `voiceName` of the PC's state (Everly while the PC names none of the four).

- **The model isn't in the APK.** The first time it's needed (reading aloud turned on, or the first reply),
  the app downloads it once from Hugging Face, 65.8 MB: `gradium/phonon-31m-en` (config, tokenizer,
  `model.q8.gguf`) and the four voices' files from `gradium/phonon-voices`, each at a pinned commit and
  checked against a pinned size and SHA-256 before it counts. It lives in the app's files (`files/phonon/`);
  a cut download resumes. Until it's ready, or on a phone that can't run it, Android's own TTS reads replies.
  Settings shows the progress, and Retry after a failure. A download runs while the app is on screen:
  Android cuts a backgrounded app's network, and the next try resumes.
- **The four voices are one list**, `Voices.ALL` in `android/.../Voices.kt` (name, catalog id, SHA-256),
  matching `VOICES` in `src/auren/voice.py`. Swapping a voice is a line in each; the phone fetches the new
  file and drops the old one.
- **Playback** streams: the reply is synthesized on one background thread into a 24 kHz float AudioTrack,
  which starts on the first chunk. Stop, a new ask or talk, and turning reading off cut it at once.
- **The library** is built from source, not committed: `android/native/build-phonon.sh` builds
  `libptts_ffi.so` (arm64-v8a, 7.6 MB, ARMv8.2 dotprod and fp16) from the pinned tag into `jniLibs/`, which is
  ignored. Without it the app builds and uses Android's TTS. The app checks the CPU has those instructions
  before loading it (one without them would crash in it). It adds 8.9 MB to the APK, with JNA.
- **Measured** on a Motorola Edge 60 Fusion (MediaTek MT6878), 2 threads: loads in about 0.2 s, the first
  audio comes 70-90 ms after a reply in the current voice (about 170 ms after switching voice), and it
  synthesizes at 0.28× real time. 4 threads gained under 10%, so it uses 2.

To measure on a phone (a debug build; nothing to tap):

```
adb shell am broadcast -n app.auren.phone/.debug.Bench --es text "A sentence to time."
adb logcat -s AurenBench AurenVoice
```

It downloads and loads the model if needed (keep the app on screen), then logs each voice's time to first
audio and real-time factor, without playing. `--es say "Hello." --es voice Hugh` reads one aloud quietly,
and `--ez stop true` stops it.

## The inbox

`send_to_phone(kind, title, content, path)` puts text, a link, an image or a file in the phone's inbox,
which lives on the PC (`~/.local/state/auren/phone/inbox.jsonl` and `files/`, the newest 200 kept). Item ids
only grow: a phone that was away asks for everything after the last id it has (the cursor), and a
connected phone gets new items as they come. Files are copied when sent (at most 50 MB). Sending a file
or text that looks like a secret (`.env`, `~/.ssh`, private key files such as `*.pem`, `*.key` or
`id_ed25519`, tokens) asks first, at every level but `never`. Auren's own files (under
`~/.config/auren` and `~/.local/state/auren`, including the server's TLS key) are never sent. The check
runs again on the file actually opened, so a link swapped after the question sends nothing.

## Share sheet

Share text, a link or an image from any Android app to Auren, then pick:

| Action | On the PC |
|---|---|
| Ask Auren | A phone turn about it (an image is saved to Downloads and its path given to the brain) |
| PC clipboard | Copied with `wl-copy` (on a Mac, the pasteboard; an image as PNG) |
| Open on PC | A link opens in the browser, an image in the viewer, text shows as a notification |

## Phone control

Auren on the PC can use the phone the way you do: open apps, read what's on the screen, tap, type, scroll
and press Back, Home and the other system keys. "Open WhatsApp and tell Mom I'm late" works from the desk.

**Turning it on.** In the app, open Settings, then Phone control, then *Turn on in Accessibility*, and turn
on **Auren phone control**. Android greys it out for an app that wasn't installed from a store: open App
info for Auren, tap ⋮, choose *Allow restricted settings*, and try again. While it's on, the app stays
connected to the PC with no screen showing, so the PC can reach the phone at any time. The microphone
still stops when the app leaves.

**How it reads.** The accessibility service (`HandsService`) reads the app in front (and the notification
shade or a system dialog when one is open) into text plus numbered items: what can be tapped, typed into,
switched or scrolled. A row with no words of its own (a chat in a list) is named by what it holds. The
brain's tools are `phone_screen`, `phone_open_app`, `phone_apps`, `phone_tap`, `phone_type`,
`phone_scroll` and `phone_key`; each action returns the screen after it, marked untrusted.

**Safety.** Phone control follows the PC's safety level. A tap on something that sends, buys, deletes or
posts asks first, and so does pressing the keyboard's action key in a field that isn't a search; Full
access asks nothing. Every tap and type names the label it was read with; the PC checks it before a
confirmed action runs, and the phone checks it again before acting, so a screen that changed meanwhile
refuses instead of tapping something else.

**Messages.** The phone sends `control.ready` (`{on}`) after each hello and whenever the service is turned
on or off. The PC sends `control` (`{id, action, ...}`) to the newest connection that has it on, one at a
time; `action` is `screen`, `tap` (`n, long, expect`), `type` (`n, text, submit, expect`), `scroll`
(`direction, n?`), `key` (`back, home, recents, notifications, quick_settings, lock`), `open` (`app`) or
`apps` (`query`). The phone answers `control.result` with `{id, ok, screen, items:[{n, label, field,
search}]}` or `{id, ok: false, error}`. The PC gives up after 20 s.

## Protocol (API v1)

Base URL `https://<address>:<port>`, any address from the QR code or the last hello. HTTPS and WSS only:
the PC makes a self-signed certificate (EC P-256, ten years) on first start, and the app trusts that one
certificate by its SHA-256 fingerprint (`fp`, of the DER bytes) and nothing else: no CA, no hostname
check. So the same pin protects the LAN, where nothing else encrypts the link, and Tailscale. The app tries
the PC's address on its current Wi-Fi subnet first, then Tailscale, then any other LAN address, the one
that worked last first in each, and shows which path it is on. A LAN address goes out over the Wi-Fi it is
on, even when Android's default network is mobile data (a Wi-Fi without internet).

Every call except `GET /v1/hello` (without a token) and `POST /v1/pair` needs
`Authorization: Bearer <device token>`; a missing, wrong or revoked token gets 401 (on the WebSocket, the
handshake is refused with 401). `/v1/share` checks the token again after the upload, so a phone revoked
meanwhile gets 401 and nothing runs.

`POST /v1/pair` is refused (403) before its body is read while no pairing is open, takes at most 4 KB
(413), and allows 10 tries a minute from everyone together (429). Refused tries are logged with the
address they came from.

| Call | Body / query | Answer |
|---|---|---|
| `GET /v1/hello` | | `{app, api, pc, addrs: [{ip, via: tailscale\|lan, later?}], fp}`, plus `device` with a valid token. `later: true` marks the PC's Tailscale address from before, given while Tailscale is off |
| `POST /v1/pair` | `{code, name}` | `{token, device, pc, api, addrs, fp}`; 403 `{error}` for a wrong, used or expired code |
| `GET /v1/inbox` | `after`, `limit` (≤ 200) | `{items, next, last}` |
| `GET /v1/inbox/{id}/file` | | the image or file (404 if gone) |
| `POST /v1/share` | `{id, kind: text\|link\|image, action: ask\|clipboard\|open, text, note, data (base64), mime}` (≤ 20 MB, else 413) | `{status}`: `ok`, `busy` (409) or `error: …` |
| `WS /v1/ws` | | below |

An inbox item: `{id, kind, title, at, text?, url?, file?: {name, size, mime}}`.

**WebSocket.** Text frames are JSON objects; binary frames are PCM audio (16 kHz, mono, s16le) for the
listen this phone started (frames from any other phone are dropped). On connect the PC sends
`{"type":"hello", api, pc, addrs, fp, device, inboxLast, state}`; the app takes its address list from
`addrs`, so a PC whose LAN address changed is found again the next time it says hello (over Tailscale, if
the old LAN address no longer answers; with Wi-Fi alone, pair again). While the PC's Tailscale is off, its
`addrs` (and the pairing code) still list its Tailscale address from when it was last up, marked `later`, so
a phone that connected only on the Wi-Fi still finds the PC from away once Tailscale is on. A hello without
any Tailscale address keeps the saved one.

Phone to PC:

| `type` | Fields | Ack `status` |
|---|---|---|
| `ask` | `id`, `text` | `ok`, `busy`, `error: …` |
| `talk.start` | `id`, `handsFree`, `fresh` | `ok`, `busy`; then stream binary frames |
| `talk.stop` | | `heard …`, `heard nothing`, `not listening` (also for a listen another phone started) |
| `cancel` | | `cancelled` (Stop: listening, thinking, working) |
| `answer` | `id`, `yes`, `ask` | `approved N`, `dropped N`, `that question is no longer open`, `nothing is waiting for an answer`, `busy` |
| `voice.set` | `id`, `name` | `ok`, or `error: no voice 'X': choose Everly, Juniper, Hugh or Trevor` for an unknown name |
| `forget` | `id` | `ok`: a fresh conversation, as the panel's Fresh (after a turn that's running) |
| `setting` | `id`, `name` (`brain`, `effort`, `codex-effort`), `value` | `ok` or `error: …`, the auren-ctl command's answer. The state carries `mode`, `effort`, `codexEffort`, `codexModel`. Not `safety`: a lost phone can't turn off Auren's questions |
| `resume` | `ids` | none: for each id, its `reply` if the PC kept it, else `lost`; nothing while it still runs |
| `sync` | `after` | none: `inbox` messages for each item after the cursor |
| `ping` | `id` | `{"type":"pong"}` |

Every request gets `{"type":"ack", id, status}`. `id` is the phone's request id: an `ask`, `talk.start` or
`answer` that phone already sent and the PC accepted (it remembers the last 500) isn't run again but acked with its first
status and `"duplicate": true`, so a phone that reconnects can safely resend; one that arrives while the
first is still running gets `in progress` with `"duplicate": true`. A resent `ask` or `talk.start` is also
treated as a `resume` of its id (the ack was lost, so the reply may have been too). A `busy` request isn't
remembered and also gets `{"type":"busy", id, owner}` first.

`voice.set` picks the voice, e.g. `{"type":"voice.set","id":"v1","name":"Hugh"}`. It is the same setting
as `auren-ctl voice-name` and the panel's voice picker (`voice.name` in `config.toml`): the PC speaks
with it, and the phone reads it back as `voiceName` in the next `state`. The name is matched ignoring
case; the ack carries no name, so the app takes the canonical one from `voiceName`. It works whatever
the PC is doing (it isn't a turn, so never `busy`) and even with the PC's voice off or not loaded; it
doesn't speak a sample on the PC. The voices are the four in `src/auren/voice.py` (`VOICES`): Everly
(the default), Juniper, Hugh and Trevor.

An `answer` names the question it was tapped on: `ask` is the `ask` field of the state that showed it (a
digest of the open confirmations). Any other value, or none, gets `that question is no longer open` and
approves nothing, so a Yes kept from before a reconnect can't approve a different action. The app never
queues or resends an answer; after a reconnect the question shows again.

**Replies after a reconnect.** The PC keeps each phone's replies for 10 minutes (the newest 200). On
hello the app sends `resume` with the requests still waiting (share-sheet asks included: their id is a
waiting line too); each gets its reply, or `lost` when the PC has neither the reply nor the turn any more,
so the phone never waits forever. An approval has nothing to resume: its effect shows in the state the
hello carries, and the turn's reply comes back under the ask's own id.

PC to phone: `state` (the [state.json](ipc.md#statejson) fields `state, detail, since, brain, safety, voice,
voiceName, awaiting, question, until, ask, transcript, reply, silent, tool, remind, fresh`, plus `origin`: `desk`,
`phone` or absent), `reply` (`{id, device, text, at}`, a phone turn's answer, sent only to that phone;
empty text when there was nothing to say), `ended` (`{id}`, the turn ended without a reply: nothing was
heard, or it was stopped), `lost` (`{id}`, after `resume` or a resend), `inbox` (`{item}`), `busy`, `revoked` (then
close code 4401), `error`. When the server stops or moves, open connections are closed (1012).

## Files

| Path | Content |
|---|---|
| `~/.local/state/auren/phone/` (mode 700) | Everything below |
| `devices.json` (600) | Paired phones: `id`, `name`, `created`, `lastSeen`, and `hash` (SHA-256 of the token) |
| `pairing.json` (600) | The open pairing: the code's hash, expiry, wrong tries |
| `inbox.jsonl`, `files/` (600) | What Auren sent to the phone |
| `cert.pem`, `key.pem` (600) | The server's self-signed certificate and its private key, made once |
| `tailscale.json` (600) | This PC's Tailscale address from when Tailscale was last up, for while it's off |
| `$XDG_RUNTIME_DIR/auren/phone.json` | The server: `on`, `idle`, `port`, `addrs` (`[{ip, via, ok, why}]`), `fp`, `why`, `missing`, `connected` (`[{id, name, via}]`) |

On the phone, the PC's addresses, its certificate fingerprint and the device token are kept in the app's
private storage. Pairing again or unpairing clears the old PC's conversation, inbox and cursor.

Tested: `tests/test_phone.py` (pairing and its limits, tokens and revoke, refused requests, one turn at a
time per phone, request ids and in-progress retries, answers bound to their question, replies kept for a
resume, the inbox cursor, `send_to_phone`, the certificate, the addresses it binds, pinned TLS with a
wrong pin refused, reload stopping and moving the server, listening from a PCM queue against a stand-in
AssemblyAI) and the app's unit tests (`android/app/src/test`: the protocol, the address order, the pin
with a real TLS handshake, the inbox file, the screen lifecycle, the voice list and the model download's checks and resume).
