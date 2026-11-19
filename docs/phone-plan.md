# Phone app plan

An Android app that is a remote for Auren on the PC: talk to it from the phone, get things back in an
inbox. Agreed 2026-11-18 (Claude draft, GPT 6.2 Sol review, Raja's decisions). Refer back to this
when judging whether v1 is done.

## Decisions

- **Native Android**, Kotlin + Jetpack Compose, in `android/` of this repo. The protocol doc and the
  daemon change together. It stays generic: no personal values, works for any Omarchy user at home or on Tailscale.
- **Transport: the same Wi-Fi and Tailscale, both over pinned TLS** (revised 2026-11-19, Raja's call:
  the phone must work at home without Tailscale). The daemon serves a small HTTPS + WSS API bound to the
  PC's Tailscale address and its private LAN addresses (RFC 1918, real interfaces; never `0.0.0.0`,
  never public, never Docker/VM/VPN). It makes a self-signed certificate once; the QR carries its SHA-256
  fingerprint and the app trusts only that certificate, so the LAN is as private as the tailnet.
  `phone.lan = false` keeps it to Tailscale. The app tries the Wi-Fi address first, then Tailscale, and
  shows which it uses. A request is accepted only with a device token issued by pairing;
  `auren phone pair` shows a QR (addresses, port, fingerprint, one-time code), `auren phone devices`
  lists, `auren phone revoke <id>` cuts a phone off.
- **Safety follows the desktop.** A request from the phone runs at the same approval level as the
  desktop (`ask`/`task`/`free`/`never`). Raja's call: the phone is his and has its own lock; losing it
  is rare and revoking a device is one command. Approval questions show on the phone as Yes/No buttons
  as well as on the desktop; either answers them.
- **One request at a time, phone and desk together.** A turn is owned from listening to the end of
  its answer. A second start returns `busy`. Stop works from either side. Each phone request has an
  ID, so a reconnect never runs it twice; a Yes/No also names the question it answers, and is never
  resent.
- **Audio goes to the PC.** The phone streams 16 kHz mono PCM; the daemon feeds it to the same ears
  (AssemblyAI, vocabulary). The API key stays on the PC.
- **Replies:** text on the phone, spoken by the phone's own text-to-speech when its voice toggle is on.
  The PC speaks only for requests made at the PC. Streaming the PC's voice to the phone is later.
- **Inbox on the PC is the source of truth.** The phone syncs by cursor when it connects and gets new
  items live while connected.

## v1 (build now)

1. Daemon `phone` server: pairing, device tokens, revoke, `whois`-free (token is the gate; bound to the
   Tailscale IP and the private LAN addresses, pinned TLS).
2. Live state mirror (what `state.json` shows) and Stop.
3. Typed requests; hold-to-talk; hands-free (send on silence, as on the desktop).
4. Approval questions as buttons.
5. `send_to_phone` tool: text, link, image, file. Inbox: open links, copy text, view/save/share images,
   open files.
6. Share sheet into Auren: share a link, text or image from any app to the PC (as a request, or just
   into the PC's clipboard / open it there).
7. Connection states the user can act on: PC unreachable (join its Wi-Fi or turn on Tailscale), daemon
   off, paired/unpaired, and the live path (Wi-Fi or Tailscale).
8. Quick Settings tile to start talking.

## Later

- Background alerts when the app is closed (agent job done or needs input): UnifiedPush or FCM ping
  that fetches over the pinned link.
- "Show me my screen" screenshot, home widget, the PC's voice on the phone, transcript review
  before running in noisy places.
- **PC → phone:** on-demand notification triage (allowlisted apps, redacted) through a
  NotificationListenerService; then typed phone actions ("open this link"); full control by mirroring
  with scrcpy (USB first) so Auren's screen hands drive it.

## Known limits

- A sleeping or powered-off PC can't be reached.
- On a shared Wi-Fi (a café, an office), others on it can reach the server's port. They see only TLS
  with a self-signed certificate and `GET /v1/hello` (name, addresses, fingerprint); without a token
  every other call is refused, and pairing is refused unless `auren phone pair` is open. Set
  `phone.lan = false` on networks you don't trust.
- Android 17's local network permission (apps targeting it) will need asking for before the LAN path
  works; the app targets 36 today.
- Anyone holding an unlocked, paired phone can do what the desktop level allows.
