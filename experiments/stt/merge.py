"""Test "let the brain combine": an LLM gets transcript(s) + vocabulary and returns what was said.

  uv run merge.py                 # every pair of providers (vocab runs), plus single-transcript fixes
  uv run merge.py --model openai/gpt-oss-120b

Uses Groq's chat API (same key) with the model Voisu uses for reconciliation.
The real assistant brain would also know what's on screen and the herdr panes,
so this is a lower bound on what combining can do.
Results go to results/merged.json: {recording_id: {"fix:<cfg>" or "pair:<a>+<b>": {...}}}
"""

from __future__ import annotations

import argparse
import itertools
import json
import time

import httpx

from common import RESULTS, api_key, load_refs, load_vocab

OUT = RESULTS / "merged.json"
SYSTEM = (
    "You turn speech-recognition output into the exact words a user said to their desktop "
    "assistant. You get one or two transcripts of the same recording from different "
    "recognizers. Each can mishear words, especially names and technical terms. The user "
    "often says the terms in the vocabulary list. Return only the words the user most "
    "likely said, as one line. Do not answer or carry out the command, and do not add "
    "words nobody said."
)


def ask(model: str, user: str) -> tuple[str, float]:
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
    if model.startswith("qwen/"):
        body["reasoning_effort"] = "none"
    for attempt in range(5):
        start = time.perf_counter()
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions", json=body, timeout=60,
                       headers={"Authorization": f"Bearer {api_key('groq')}",
                                "User-Agent": "parth-stt-experiment/0.1"})
        if r.status_code == 429 or r.status_code >= 500:  # rate limit or temporary outage
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        return text.split("</think>")[-1].strip(), round(time.perf_counter() - start, 3)
    raise RuntimeError("rate limited")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="qwen/qwen3.8-27b")
    args = p.parse_args()

    transcripts = json.loads((RESULTS / "transcripts.json").read_text())
    refs, vocab = load_refs(), load_vocab()
    merged = json.loads(OUT.read_text()) if OUT.exists() else {}
    vocab_line = ", ".join(vocab)

    for sid in sorted(refs):
        runs = {k.split("|")[0]: v["text"] for k, v in transcripts.get(sid, {}).items()
                if k.endswith("|vocab") and v.get("text")}
        jobs = [(f"fix:{p}", f"Vocabulary: {vocab_line}\n\nTranscript: {t}") for p, t in runs.items()]
        for a, b in itertools.combinations(sorted(runs), 2):
            jobs.append((f"pair:{a}+{b}",
                         f"Vocabulary: {vocab_line}\n\nTranscript A: {runs[a]}\nTranscript B: {runs[b]}"))
        for key, prompt in jobs:
            key = f"{key}@{args.model}"
            if key in merged.get(sid, {}):
                continue
            text, secs = ask(args.model, prompt)
            merged.setdefault(sid, {})[key] = {"text": text, "seconds": secs}
            print(f"  {sid} {key[:60]:60} {text[:60]}", flush=True)
        OUT.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    print(f"saved {OUT}. Next: uv run score.py")


if __name__ == "__main__":
    main()
