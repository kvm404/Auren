"""Latency of a Claude Code process that stays running (the "keep the agent open" setup).

  uv run warm.py                     # Haiku, thinking off, 10 cases
  uv run warm.py --model sonnet --n 6

One `claude -p --input-format stream-json` process gets the cases one after
another as new user messages, so there is no start-up cost per request. The
conversation grows with every request (as a long-running brain's would), so
this measures speed, not accuracy. Results go to results/warm.jsonl.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import prompt
from backends import EMPTY_DIR

HERE = Path(__file__).parent


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="haiku")
    p.add_argument("--think", action="store_true")
    p.add_argument("--n", type=int, default=10)
    a = p.parse_args()
    cases = [c for c in json.loads((HERE / "cases.json").read_text()) if c["kind"] == "clean"][: a.n]
    start = time.perf_counter()
    proc = subprocess.Popen(
        ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
         "--model", a.model, "--settings", json.dumps({"alwaysThinkingEnabled": a.think}), "--effort", "low",
         "--system-prompt", prompt.system_prompt(), "--tools", "", "--json-schema", prompt.schema_json(),
         "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "project"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=EMPTY_DIR, bufsize=1)
    out = (HERE / "results" / "warm.jsonl").open("a")
    for i, case in enumerate(cases):
        msg = {"type": "user", "message": {"role": "user", "content": prompt.user_message(case["text"])}}
        sent = time.perf_counter()
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        first = None
        for line in proc.stdout:
            ev = json.loads(line)
            if ev.get("type") == "assistant" and first is None:
                first = time.perf_counter() - sent
            if ev.get("type") == "result":
                total = time.perf_counter() - sent
                u = ev.get("usage", {})
                row = {"model": a.model, "think": a.think, "i": i, "case": case["id"], "total_s": round(total, 2),
                       "first_event_s": round(first, 2) if first else None,
                       "api_s": ev.get("duration_api_ms", 0) / 1000,
                       "context_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                       + u.get("cache_creation_input_tokens", 0),
                       "plan": ev.get("structured_output")}
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                note = " (includes start-up)" if i == 0 else ""
                print(f"{case['id']:4} total {total:5.2f}s  api {row['api_s']:5.2f}s  "
                      f"context {row['context_tokens']:6}{note}  {(row['plan'] or {}).get('say', '')[:60]}",
                      flush=True)
                break
    print(f"process alive for {time.perf_counter() - start:.0f}s")
    proc.stdin.close()
    proc.wait(timeout=30)


if __name__ == "__main__":
    main()
