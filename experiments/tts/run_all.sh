#!/usr/bin/env bash
# Benchmark every engine, each in its own process, then print the comparison.
set -u
cd "$(dirname "$0")"
engines=("$@")
[ ${#engines[@]} -eq 0 ] && engines=(supertonic-fast supertonic)
for e in "${engines[@]}"; do
  echo "=== $e"
  uv run --quiet bench.py "$e" || echo "!!! $e failed"
done
uv run --quiet compare.py
