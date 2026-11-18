# Phone remote

The Android app (`android/`) is a remote for the Auren on your PC: talk or type to it from your phone,
see what it's doing, answer its questions, stop it, and get what it sends you. The PC does all the work;
the phone is a microphone, a screen and a few buttons. The plan behind it is [phone-plan.md](phone-plan.md).

## Setting it up

1. Install Tailscale on the PC and the phone, signed in to the same tailnet. The PC's server listens only
   on its Tailscale address (`tailscale ip -4`), never on `0.0.0.0` or the local network.
2. Build and install the app: `cd android && ./gradlew installDebug` (or copy
   `android/app/build/outputs/apk/debug/app-debug.apk` to the phone).
3. On the PC, run `auren phone pair`. It prints a QR code, the same thing as text (`auren://pair?...`), and
   waits. Scan the QR with the app, or paste the text. The code works once, for 5 minutes, and 5 wrong
   codes cancel it.
4. `auren doctor` has a **Phone** section: whether the server is on, on which address, and why not.

| Command | Does |
|---|---|
| `auren phone pair [--no-wait] [--no-qr]` | A new one-time code (any earlier one stops working). Needs the daemon's server to be on |
| `auren phone devices` | The paired phones: id, name, when paired, last seen |
| `auren phone revoke <id>` | Unpair one. Its token stops working at once and its open connection is closed |

`config.toml`:

```toml
[phone]
enabled = "auto"   # auto or true: serve when Tailscale is up; false: never
port = 8735
```

With Tailscale down the server stays off and tries again every 30 s; it moves with the address when it
changes. `send_to_phone` and its prompt rule are offered to the brain only while at least one phone is
paired (pairing or revoking the last phone starts a fresh conversation so the brain's tools match).

## How a phone turn works

- **One turn at a time, across the desk and the phone.** A turn is owned from the moment listening starts
  (or a typed request comes in) until the answer ends. While the desk owns it, the phone's requests get
  `busy`; while the phone owns it, Super+A, `auren-ctl talk/start/ask` say "Auren is busy with your
  phone". **Stop works from either side**: the phone's Stop, or at the desk Super+A while it thinks or works, the
  panel's Stop or `auren-ctl cancel`.
- **The desk's safety level applies.** Approvals show on the phone (Yes / No) and on the desk (bubble,
  panel, `auren-ctl yes|no`); either answer counts, the same way as [the buttons](safety.md#who-reads-the-answer),
  and what follows (the action, the brain carrying on) is a turn of the side that answered.
- **Replies go to the phone as text**, and the phone may read them aloud (Android TTS, a setting). The PC
  doesn't speak a phone turn's reply and doesn't pop up notices for it; the mascot still shows the state.
- **Voice:** the phone records 16 kHz mono 16-bit PCM and streams it over the WebSocket into the same
  speech pipeline as the PC's microphone (AssemblyAI). Hold to talk, or hands-free: it sends when you stop
  talking, like auto-send.
- The brain is told the request came from the phone, so it keeps replies short and uses `send_to_phone`
  for links, files and images.

## The inbox

`send_to_phone(kind, title, content, path)` puts text, a link, an image or a file in the phone's inbox,
which lives on the PC (`~/.local/state/auren/phone/inbox.jsonl` and `files/`, the newest 200 kept). Item ids
only grow: a phone that was away asks for everything after the last id it has (the cursor), and a
connected phone gets new items as they come. Files are copied when sent (at most 50 MB). Sending a file
or text that looks like a secret (`.env`, keys, tokens) asks first, at every level but `never`.

## Share sheet

Share text, a link or an image from any Android app to Auren, then pick:

| Action | On the PC |
|---|---|
| Ask Auren | A phone turn about it (an image is saved to Downloads and its path given to the brain) |
| PC clipboard | Copied with `wl-copy` |
| Open on PC | A link opens in the browser, an image in the viewer, text shows as a notification |

## Protocol (API v1)

Base URL `http://<tailscale ip>:<port>`. Every call except `GET /v1/hello` (without a token) and
`POST /v1/pair` needs `Authorization: Bearer <device token>`; a missing, wrong or revoked token gets 401
(on the WebSocket, the handshake is refused with 401). The traffic is plain HTTP inside Tailscale's
encrypted tunnel; the app allows cleartext only to `100.64.0.0/10` addresses.

| Call | Body / query | Answer |
|---|---|---|
| `GET /v1/hello` | | `{app, api, pc}`, plus `device` with a valid token |
| `POST /v1/pair` | `{code, name}` | `{token, device, pc, api}`; 403 `{error}` for a wrong, used or expired code |
| `GET /v1/inbox` | `after`, `limit` (≤ 200) | `{items, next, last}` |
| `GET /v1/inbox/{id}/file` | | the image or file (404 if gone) |
| `POST /v1/share` | `{id, kind: text\|link\|image, action: ask\|clipboard\|open, text, note, data (base64), mime}` (≤ 20 MB) | `{status}`: `ok`, `busy` (409) or `error: …` |
| `WS /v1/ws` | | below |

An inbox item: `{id, kind, title, at, text?, url?, file?: {name, size, mime}}`.

**WebSocket.** Text frames are JSON objects; binary frames are PCM audio (16 kHz, mono, s16le) for the
listen this phone started. On connect the PC sends
`{"type":"hello", api, pc, device, inboxLast, state}`.

Phone to PC:

| `type` | Fields | Ack `status` |
|---|---|---|
| `ask` | `id`, `text` | `ok`, `busy`, `error: …` |
| `talk.start` | `id`, `handsFree`, `fresh` | `ok`, `busy`; then stream binary frames |
| `talk.stop` | | `heard …`, `heard nothing`, `not listening` |
| `cancel` | | `cancelled` (Stop: listening, thinking, working) |
| `answer` | `id`, `yes` | `approved N`, `dropped N`, `nothing is waiting for an answer`, `busy` |
| `sync` | `after` | none: `inbox` messages for each item after the cursor |
| `ping` | `id` | `{"type":"pong"}` |

Every request gets `{"type":"ack", id, status}`. `id` is the phone's request id: an `ask`, `talk.start` or
`answer` that phone already sent and the PC accepted (it remembers the last 500) isn't run again but acked with its first
status and `"duplicate": true`, so a phone that reconnects can safely resend. A `busy` request isn't
remembered and also gets `{"type":"busy", id, owner}` first.

PC to phone: `state` (the [state.json](ipc.md#statejson) fields `state, detail, since, brain, safety, voice,
awaiting, question, until, transcript, reply, silent, tool, remind, fresh`, plus `origin`: `desk`, `phone`
or absent), `reply` (`{id, device, text, at}`, a phone turn's answer; empty text when there was nothing to
say), `inbox` (`{item}`), `busy`, `revoked` (then close code 4401), `error`.

## Files

| Path | Content |
|---|---|
| `~/.local/state/auren/phone/` (mode 700) | Everything below |
| `devices.json` (600) | Paired phones: `id`, `name`, `created`, `lastSeen`, and `hash` (SHA-256 of the token) |
| `pairing.json` (600) | The open pairing: the code's hash, expiry, wrong tries |
| `inbox.jsonl`, `files/` (600) | What Auren sent to the phone |
| `$XDG_RUNTIME_DIR/auren/phone.json` | The server: `on`, `host`, `port`, `why` (when off) |

On the phone, the PC's address and the device token are kept in the app's private storage.

Tested: `tests/test_phone.py` (pairing, tokens and revoke, refused requests, one turn at a time, request
ids, the inbox cursor, `send_to_phone`, listening from a PCM queue against a stand-in AssemblyAI) and the
app's protocol unit tests (`android/app/src/test`).
