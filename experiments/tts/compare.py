"""Summarize results/*.json, or play one sample across every engine.

  uv run compare.py              # table
  uv run compare.py play status  # each engine says its name, then plays "status"
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent


def load_results() -> list[dict]:
    paths = sorted((HERE / "results").glob("*.json"))
    return [json.loads(p.read_text()) for p in paths if not p.name.startswith("resources-")]


def table() -> None:
    results = load_results()
    if not results:
        raise SystemExit("no results yet; run ./run_all.sh first")
    print(f"{'engine':16} {'load':>6} {'RAM MB':>7} {'ack 1st':>8} {'short 1st':>10} "
          f"{'status 1st':>11} {'long x-rt':>10} {'mean x-rt':>10}")
    for r in results:
        s = {row["sample"]: row for row in r["samples"]}
        en = [row for row in r["samples"] if row["sample"] != "hindi"]
        mean = sum(row["x_realtime"] for row in en) / len(en)
        print(f"{r['engine']:16} {r['load_s']:5.1f}s {r['peak_rss_mb']:7} "
              f"{s['ack']['first_audio_s']:7.2f}s {s['short']['first_audio_s']:9.2f}s "
              f"{s['status']['first_audio_s']:10.2f}s {s['long']['x_realtime']:9.1f}x {mean:9.1f}x")
    print("\n1st = seconds until the first audio is ready. x-rt = seconds of speech made per second "
          "(above 1 means it never stalls once started).")


def play(sample: str) -> None:
    """Each engine says its own name, then plays the sample, one engine at a time."""
    for r in load_results():
        folder = HERE / "out" / r["engine"]
        wav = folder / f"{sample}.wav"
        if not wav.exists():
            continue
        print(f"▶ {r['engine']} ({r['voice']})", flush=True)
        intro = folder / "_intro.wav"
        if intro.exists():
            subprocess.run(["pw-play", str(intro)], check=False)
            time.sleep(0.4)
        subprocess.run(["pw-play", str(wav)], check=False)
        time.sleep(1.0)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "play":
        play(sys.argv[2])
    else:
        table()
