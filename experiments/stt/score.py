"""Score everything against what you actually said.

  uv run score.py            # leaderboard
  uv run score.py --errors   # also show each config's mistakes, line by line

WER = word errors / words said (lower is better). Terms = names and technical
words from vocab.txt that came out right (space- and case-insensitive).
"Best pick" = if you could always choose the better of two providers per recording:
the ceiling for combining two providers.
"""

from __future__ import annotations

import itertools
import json
import statistics
import sys

from common import RESULTS, load_refs, load_vocab, term_hits, wer


def load(name: str) -> dict:
    path = RESULTS / name
    return json.loads(path.read_text()) if path.exists() else {}


def main() -> None:
    refs, vocab = load_refs(), load_vocab()
    runs = load("transcripts.json")
    merged = load("merged.json")
    if not runs:
        raise SystemExit("no results yet; run: uv run run.py")

    table: dict[str, dict[str, dict]] = {}   # config -> sid -> result
    for source in (runs, merged):
        for sid, by_cfg in source.items():
            if sid in refs:
                for cfg, res in by_cfg.items():
                    table.setdefault(cfg, {})[sid] = res

    rows = []
    for cfg, by_sid in table.items():
        errs = words = hit = total = 0
        secs, failed = [], 0
        for sid, ref in refs.items():
            res = by_sid.get(sid)
            if not res or res.get("error"):
                failed += 1
                continue
            e, n = wer(ref, res["text"])
            h, t, _ = term_hits(ref, res["text"], vocab)
            errs, words, hit, total = errs + e, words + n, hit + h, total + t
            if res.get("seconds"):
                secs.append(res["seconds"])
        if words:
            rows.append((errs / words, cfg, hit, total, statistics.median(secs) if secs else 0, failed))

    # Ceiling for picking the better of two providers per recording (vocab runs only).
    vocab_cfgs = [c for c in table if c.endswith("|vocab")]
    for a, b in itertools.combinations(sorted(vocab_cfgs), 2):
        errs = words = hit = total = 0
        for sid, ref in refs.items():
            ra, rb = table[a].get(sid, {}), table[b].get(sid, {})
            cands = [r["text"] for r in (ra, rb) if r.get("text")]
            if not cands:
                continue
            best = min(cands, key=lambda t: wer(ref, t)[0])
            e, n = wer(ref, best)
            h, t, _ = term_hits(ref, best, vocab)
            errs, words, hit, total = errs + e, words + n, hit + h, total + t
        if words:
            name = f"best pick: {a.split('|')[0]} + {b.split('|')[0]}"
            rows.append((errs / words, name, hit, total, 0, 0))

    rows.sort()
    n = len(refs)
    print(f"{n} recordings, {sum(len(r.split()) for r in refs.values())} words, "
          f"{sum(len(term_hits(r, '', vocab)[2]) for r in refs.values())} vocabulary terms\n")
    print(f"{'config':66} {'WER':>6} {'terms':>8} {'median s':>9}")
    for rate, cfg, hit, total, med, failed in rows:
        terms = f"{hit}/{total}" if total else "-"
        lat = f"{med:.2f}" if med else ""
        note = f"  ({failed} failed)" if failed else ""
        print(f"{cfg[:66]:66} {rate:6.1%} {terms:>8} {lat:>9}{note}")

    if "--errors" in sys.argv:
        for _, cfg, *_ in rows:
            if cfg.startswith("best pick") or cfg not in table:
                continue
            print(f"\n=== {cfg}")
            for sid, ref in sorted(refs.items()):
                res = table[cfg].get(sid, {})
                text = res.get("text", "")
                e, _ = wer(ref, text)
                if e:
                    _, _, missed = term_hits(ref, text, vocab)
                    print(f"  {sid} [{e} err] {text}" + (f"   missed: {', '.join(missed)}" if missed else ""))
                    print(f"  {'':3} said:   {ref}")


if __name__ == "__main__":
    main()
