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

## Learn my voice

Everyone says "Auren" a little differently, and the small model on the laptop writes what it hears ("or
in", "orin", "oran"...). If it misses you, teach it: **Learn my voice** on the panel's Home page (under
Hands-free), or `auren-ctl handsfree learn`. The mascot's bubble says what to do:

1. "Say “Auren”, the way you'd call me": four times, with a pause after each (a chime for each one heard).
2. "Now say something without my name": anything, so it can drop spellings that are ordinary words for you.
3. "Done": what it heard is saved in `handsfree.heard_as` and used at once. **Forget** clears it.

Run it again after changing microphones, or if it starts missing you. Super+A stops it.

## Privacy and cost

- The microphone stays open while hands-free is on, but only the small speech model on this laptop hears it.
  Audio goes to the speech service (AssemblyAI) only after the name: from just before it until you pause.
- While another app records from a microphone (a dictation app, a call), the spotter pauses
  (`pause_when_mic_busy`), so talking about Auren there doesn't wake it. The panel says "Paused".
- The local model uses about 7% of one core and 150 MB of memory. A false start costs a few seconds of
  speech service time (it bills per second).

## Settings (`[handsfree]` in `~/.config/auren/config.toml`)

| Key | Default | |
|---|---|---|
| `enabled` | `false` | Hands-free on or off (the panel's switch, `auren-ctl handsfree on\|off\|toggle`) |
| `sensitivity` | `medium` | `low` (one-word spellings only, like "orin"), `medium` (also two-word ones at the start of a phrase, like "or in"), `high` (looser ones too). Panel, or `auren-ctl handsfree sensitivity high` |
| `heard_as` | `[]` | How the local model hears *you* say the name (Learn my voice fills it in), e.g. `["or in", "morn"]` |
| `follow_up` | `questions` | Listen again after a reply without the name: `off`, `questions` (Auren asked something), `always`. Panel, or `auren-ctl handsfree follow-up always` |
| `end_silence` | `1.2` | Seconds of silence that end a request (0.6 to 4). A finished sentence ends after 0.5 s |
| `chime` | `true` | The two-note chime when it hears its name |
| `pause_when_mic_busy` | `true` | Pause while another app records |

## Tuning it to your voice

Learn my voice first (above). To see what the local model hears:

```bash
auren wake-test                     # 60 s: say "Auren" a few times, and other things; prints every phrase and each hit
auren wake-test --sensitivity high
```

If it misses the name, try `high`, or say the name on its own with a short pause. If it wakes up when
you didn't call it, try `low`. False starts from other names that sound alike are turned away by the
transcript check, so they cost a chime but no answer.

## How it works

`src/auren/wake.py`, decision [0004](decisions/0004-wake-word.md).

1. **Open mic:** one `pw-record` named `auren-mic` (16 kHz mono), reopened if the device goes away.
2. **Spotter:** a small streaming speech-to-text model (sherpa-onnx, a ~70M-parameter streaming zipformer) in its
   own thread, fed 50 ms chunks. `wake.find_name` looks for the name in its words: one-word spellings
   anywhere, two-word ones where a phrase starts (after a pause, or "hey"/"ok"), and the user's own from
   Learn my voice. A name at the end of a phrase counts once a model step (~0.3 s) left it unchanged, so "morn"
   isn't taken before it becomes "morning". The model is in `~/.local/share/auren/models/asr-en-70m/`
   (`auren wake-model`; install.sh fetches it, and turning hands-free on fetches it if it's missing).
3. **The name heard:** the daemon (`Daemon._wake`) ignores it while listening, while paused, or while
   Auren's own voice is saying a sentence with the name in it (and for 1 s after it). Otherwise it starts
   a listen fed from the open mic with the audio from just before the name (the model says where its
   words start; up to 3 s back), so the transcript can be checked for the
   name. While thinking or working the listen starts first and the answer is stopped after it, so nothing
   you say is lost; while speaking the voice stops and no audio from before the name is sent (that would
   be Auren's own voice). Super+A keeps its own recorder, so turning hands-free off never cuts a key turn.
4. **The request:** AssemblyAI with turn detection on (500 ms after a finished sentence, `end_silence`
   otherwise). `Daemon._end_on_pause` checks the first words for the name (`Daemon._for_me`), keeps
   listening if the name was all so far, and sends the rest (`wake.strip_wake`) at the pause. The speech
   service doesn't always write the name (for some voices "Auren." comes out "All right."): when the local
   model heard it where a phrase starts, the text counts anyway: if that model heard nothing after the
   name, a short first turn (three words or fewer) is the name, otherwise it all goes as it is. One
   starting with another name ("Hey Lauren…", `wake.LOOKALIKE`) or about Auren ("Auren's…") is dropped.
   After a gap in the audio (paused, or the model fell behind) the local model starts over.
5. **After the reply:** with `follow_up`, a listen with no name starts by itself and ends quietly if
   nothing is said in 5 s.

`state.json` has `handsfree` (`off`, `starting`, `on`, `paused`, `error`) and `handsfreeOptions`; a
listen started by the name has `wake: true` (`followUp: true` after a reply), which the mascot and the
panel use for their hints.

## Limits

- The name is heard 0.5 to 1 s after you finish saying it (the chime comes then); what you said meanwhile
  isn't lost.
- With speakers, Auren hears itself. It ignores the name while it is saying it, but sound-alikes in a reply
  can still stop it. Headphones, or PipeWire's echo cancellation, help.
- English only, like the rest of Auren.
