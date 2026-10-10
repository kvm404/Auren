"""Transcribe every recording with every available provider, with and without vocabulary.

  uv run run.py                          # all available providers (cached; reruns only what's missing)
  uv run run.py --only deepgram-nova3    # one provider
  uv run run.py --fresh                  # ignore the cache

Results go to results/transcripts.json:
  {recording_id: {"<provider>|vocab" or "<provider>|plain": {"text", "seconds", "error"}}}
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

from common import RECORDINGS, RESULTS, load_refs, load_vocab
from providers import PROVIDERS, available

OUT = RESULTS / "transcripts.json"


def transcribe(provider: str, sid: str, vocab: list[str] | None) -> dict:
    fn, _ = PROVIDERS[provider]
    wav = RECORDINGS / f"{sid}.wav"
    for attempt in range(4):
        start = time.perf_counter()
        try:
            text = fn(wav, vocab)
            return {"text": text, "seconds": round(time.perf_counter() - start, 3)}
        except Exception as e:  # rate limits: back off and retry
            if "HTTP 429" in str(e) and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            return {"text": "", "seconds": None, "error": str(e)[:300]}
    return {"text": "", "seconds": None, "error": "gave up"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", action="append", help="provider name (repeatable)")
    p.add_argument("--fresh", action="store_true")
    args = p.parse_args()

    refs = load_refs()
    if not refs:
        raise SystemExit("no recordings yet; run: uv run record.py")
    vocab = load_vocab()
    if not vocab:
        raise SystemExit("no vocab.txt; run: uv run vocab.py")
    providers = args.only or available()
    missing = [n for n in PROVIDERS if n not in available()]
    print(f"providers: {', '.join(providers)}" + (f"  (no key: {', '.join(missing)})" if missing else ""))

    RESULTS.mkdir(exist_ok=True)
    results = {} if args.fresh or not OUT.exists() else json.loads(OUT.read_text())

    jobs = []
    for sid in sorted(refs):
        for provider in providers:
            for mode, v in (("plain", None), ("vocab", vocab)):
                key = f"{provider}|{mode}"
                cached = results.get(sid, {}).get(key)
                if cached and not cached.get("error"):
                    continue
                jobs.append((sid, provider, key, v))

    print(f"{len(jobs)} transcriptions to run")
    # One worker per provider so requests to the same API don't overlap and skew latency.
    by_provider: dict[str, list] = {}
    for job in jobs:
        by_provider.setdefault(job[1], []).append(job)

    def run_provider(batch):
        for sid, provider, key, v in batch:
            res = transcribe(provider, sid, v)
            results.setdefault(sid, {})[key] = res
            mark = "✗ " + res["error"][:80] if res.get("error") else f"{res['seconds']:.2f}s  {res['text'][:70]}"
            print(f"  {sid} {key:28} {mark}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, len(by_provider))) as pool:
        list(pool.map(run_provider, by_provider.values()))

    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nsaved {OUT}. Next: uv run merge.py, then uv run score.py")


if __name__ == "__main__":
    main()
