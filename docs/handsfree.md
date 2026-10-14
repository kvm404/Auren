# Hands-free: "Auren, …"

Say **"Auren"** instead of pressing Super+A. It works like a smart speaker: Auren chimes, listens, and
sends what you said when you pause. The keys and the mascot still work as before.

Turn it on from the bar panel's Home page (**Hands-free**), by voice ("turn on hands-free" after Super+A),
or with `auren-ctl handsfree on`. Optional key: Super+Alt+A runs `auren-ctl handsfree toggle` (install.sh
prints the binding). Turn it off the same ways, or say "Auren, stop listening".

## How it behaves

| You say | Auren |
|---|---|
| "Auren, open Firefox" (one breath) | chimes, then sends "Open Firefox" when you pause |
| "Auren" … "what's the weather" | chimes, waits up to 8 s for the request |
| "Auren" and nothing | goes back to waiting ("Didn't catch that") |
| "Auren" while it thinks or works | stops it and listens (like Super+A) |
| "Auren" while it speaks | stops talking and listens |
| "Auren" inside your request ("ask Auren's Codex tab…") | nothing: it's already listening, so it doesn't start over |
| "Auren, stop" / "never mind" / "thanks" | stops, nothing goes to the brain |
| An answer to its question ("Which tab?" … "the second") | no name needed: after a question it listens 5 s by itself (`follow_up`) |
| "Auren, stop listening" / "turn off hands-free" | turns hands-free off |
| "I told Lauren about it" | nothing: the transcript has to start with the name |

## Privacy and cost

- The microphone stays open while hands-free is on, but only the keyword spotter on this laptop hears it.
  Audio goes to the speech service (AssemblyAI) only after the name: from 1.5 s before it until you pause.
- While another app records from a microphone (a dictation app, a call), the spotter pauses
  (`pause_when_mic_busy`), so talking about Auren there doesn't wake it. The panel says "Paused".
- The spotter uses about 2% of one core and 35 MB of memory. A false start costs a few seconds of speech
  service time (it bills per second).

## Settings (`[handsfree]` in `~/.config/auren/config.toml`)

| Key | Default | |
|---|---|---|
| `enabled` | `false` | Hands-free on or off (the panel's switch, `auren-ctl handsfree on\|off\|toggle`) |
| `sensitivity` | `medium` | `low` (fewer false starts), `medium`, `high` (hears the name more often, and other words too). Panel, or `auren-ctl handsfree sensitivity high` |
| `follow_up` | `questions` | Listen again after a reply without the name: `off`, `questions` (Auren asked something), `always`. Panel, or `auren-ctl handsfree follow-up always` |
| `end_silence` | `1.2` | Seconds of silence that end a request (0.6 to 4). A finished sentence ends after 0.5 s |
| `chime` | `true` | The two-note chime when it hears its name |
| `pause_when_mic_busy` | `true` | Pause while another app records |

## Tuning it to your voice

```bash
auren wake-test                     # 60 s: say "Auren" a few times, and other things; prints each hit
auren wake-test --sensitivity high
```

If it misses the name, try `high`, or say the name on its own with a short pause. If it wakes up when
you didn't call it, try `low`. False starts from other names that sound alike are turned away by the
transcript check, so they cost a chime but no answer.

## How it works

`src/auren/wake.py`, decision [0004](decisions/0004-wake-word.md).

1. **Open mic:** one `pw-record` named `auren-mic` (16 kHz mono), reopened if the device goes away.
2. **Spotter:** sherpa-onnx's keyword spotter in its own thread, fed 50 ms chunks. The model is in
   `~/.local/share/auren/models/kws-gigaspeech/` (`auren wake-model`; install.sh fetches it, and turning
   hands-free on fetches it if it's missing).
3. **The name heard:** the daemon (`Daemon._wake`) ignores it while listening, or while Auren's own voice is
   saying a sentence with the name in it. From idle it starts a listen fed from the open mic, with the
   last 1.5 s of audio. While thinking or working it stops the answer first; while speaking it stops the
   voice, and sends no audio from before the name (that would be Auren's own voice).
4. **The request:** AssemblyAI with turn detection on (500 ms after a finished sentence, `end_silence`
   otherwise). `Daemon._end_on_pause` checks the first words for the name (`wake.addressed`), keeps
   listening if the name was all so far, and sends the rest (`wake.strip_wake`) at the pause.
5. **After the reply:** with `follow_up`, a listen with no name starts by itself and ends quietly if
   nothing is said in 5 s.

`state.json` has `handsfree` (`off`, `starting`, `on`, `paused`, `error`) and `handsfreeOptions`; a
listen started by the name has `wake: true` (`followUp: true` after a reply), which the mascot and the
panel use for their hints.

## Limits

- Tuned on synthetic voices: run `auren wake-test` on yours.
- With speakers, Auren hears itself. It ignores the name while it is saying it, but sound-alikes in a reply
  can still stop it. Headphones, or PipeWire's echo cancellation, help.
- English only, like the rest of Auren.
