"""Measure what one engine costs this laptop: RAM idle and peak, CPU while generating.

  uv run resources.py supertonic-fast
  ./run_resources.sh          # every engine, then a table

CPU is counted from the process's own user+sys time, so it includes every
thread the engine spins up. "cores busy" = CPU seconds / wall seconds
(4.0 would mean all four hardware threads flat out).
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
from pathlib import Path

import engines
from bench import SAMPLES

HERE = Path(__file__).parent


def rss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def cpu_s() -> float:
    t = os.times()
    return t.user + t.system


def main() -> None:
    name = sys.argv[1]
    base_rss = rss_mb()
    engine, voice = engines.load(name)
    engine.prepare(voice)
    for _ in engine.stream("Hello there.", voice):  # warm up
        pass
    idle_rss = rss_mb()

    texts = [SAMPLES[s][1] for s in ("short", "status", "tricky", "long")]
    cpu0, wall0, samples = cpu_s(), time.perf_counter(), 0
    for text in texts:
        for chunk in engine.stream(text, voice):
            samples += len(chunk)
    cpu = cpu_s() - cpu0
    wall = time.perf_counter() - wall0
    audio = samples / engine.sample_rate
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    row = {
        "engine": name,
        "python_baseline_mb": round(base_rss),
        "idle_ram_mb": round(idle_rss),
        "peak_ram_mb": round(peak),
        "cores_busy": round(cpu / wall, 2),
        "cpu_s_per_audio_s": round(cpu / audio, 3),
        "audio_s": round(audio, 1),
    }
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / f"resources-{name}.json").write_text(json.dumps(row, indent=2))
    print(json.dumps(row))


if __name__ == "__main__":
    main()
