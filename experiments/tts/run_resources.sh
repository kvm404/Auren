#!/usr/bin/env bash
# Measure RAM and CPU for every engine (one process each), then print a table.
set -u
cd "$(dirname "$0")"
engines=("$@")
[ ${#engines[@]} -eq 0 ] && engines=(supertonic-fast supertonic)
for e in "${engines[@]}"; do
  uv run --quiet resources.py "$e" 2>/dev/null | tail -1 || echo "!!! $e failed"
done
uv run --quiet python - <<'PY'
import json, glob
rows = [json.load(open(f)) for f in sorted(glob.glob("results/resources-*.json"))]
rows.sort(key=lambda r: r["cpu_s_per_audio_s"])
print(f"\n{'engine':16} {'idle RAM':>9} {'peak RAM':>9} {'cores busy':>11} {'CPU-s per 1s speech':>20}")
for r in rows:
    print(f"{r['engine']:16} {r['idle_ram_mb']:7} MB {r['peak_ram_mb']:7} MB {r['cores_busy']:10.2f} {r['cpu_s_per_audio_s']:19.3f}")
print("\nRAM includes Python itself (~" + str(rows[0]['python_baseline_mb']) + " MB). Cores busy: out of 4 hardware threads.")
print("CPU-s per 1s speech: lower = cheaper; 0.25 means one core works 0.25 s for each second spoken.")
PY
